import server
import folder_paths
import os
import subprocess
import re

import asyncio
try:
    # IMPORTANT: keep this optional so the node package can still load without PyAV.
    import av
except ImportError:
    av = None

from .logger import logger
from .utils import is_url, get_sorted_dir_files_from_directory, ffmpeg_path, \
        validate_sequence, is_safe_path, strip_path, try_download_video, ENCODE_ARGS, \
        debug_log
from comfy.k_diffusion.utils import FolderOfImages


web = server.web


def error_response(status, message):
    debug_log("server_error", status=status, message=message)
    return web.Response(status=status, text=message)


def parse_int(query, key, default=0, minimum=None):
    try:
        value = int(float(query.get(key, default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def parse_float(query, key, default=0.0, minimum=None):
    try:
        value = float(query.get(key, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value

@server.PromptServer.instance.routes.get("/vhs/viewvideo")
@server.PromptServer.instance.routes.get("/viewvideo")
async def view_video(request):
    query = request.rel_url.query
    path_res = await resolve_path(query)
    if isinstance(path_res, web.Response):
        return path_res
    file, filename, output_dir = path_res

    if ffmpeg_path is None:
        #Don't just return file, that provides  arbitrary read access to any file
        if is_safe_path(output_dir, strict=True):
            return web.FileResponse(path=file)
        return error_response(503, "ffmpeg is unavailable and the requested preview path is not allowed for direct file serving.")

    frame_rate = parse_float(query, 'frame_rate', 8.0, 0.0)
    if query.get('format', 'video') == "folder":
        os.makedirs(folder_paths.get_temp_directory(), exist_ok=True)
        concat_file = os.path.join(folder_paths.get_temp_directory(), "image_sequence_preview.txt")
        skip_first_images = parse_int(query, 'skip_first_images', 0, 0)
        select_every_nth = parse_int(query, 'select_every_nth', 1, 1)
        valid_images = get_sorted_dir_files_from_directory(file, skip_first_images, select_every_nth, FolderOfImages.IMG_EXTENSIONS)
        if len(valid_images) == 0:
            return error_response(204, "No valid images were found for folder preview.")
        with open(concat_file, "w") as f:
            f.write("ffconcat version 1.0\n")
            for path in valid_images:
                f.write("file '" + os.path.abspath(path) + "'\n")
                f.write("duration 0.125\n")
        in_args = ["-safe", "0", "-i", concat_file]
    else:
        in_args = ["-i", file]
        if '%' in file:
            in_args = ['-framerate', str(frame_rate)] + in_args
    #Do prepass to pull info
    #breaks skip_first frames if this default is ever actually needed
    base_fps = 30
    try:
        proc = await asyncio.create_subprocess_exec(ffmpeg_path, *in_args, '-t',
                                   '0','-f', 'null','-', stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        _, res_stderr = await proc.communicate()

        match = re.search(': Video: (\\w+) .+, (\\d+) fps,', res_stderr.decode(*ENCODE_ARGS))
        if match:
            base_fps = float(match.group(2))
            if match.group(1) == 'vp9':
                #force libvpx for transparency
                in_args = ['-c:v', 'libvpx-vp9'] + in_args
    except subprocess.CalledProcessError as e:
        print("An error occurred in the ffmpeg prepass:\n" \
                + e.stderr.decode(*ENCODE_ARGS))
        return web.Response(status=500)
    vfilters = []
    target_rate = parse_float(query, 'force_rate', 0.0, 0.0) or base_fps
    modified_rate = target_rate / parse_int(query, 'select_every_nth', 1, 1)
    start_time = 0
    if 'start_time' in query:
        start_time = parse_float(query, 'start_time', 0.0, 0.0)
    elif parse_float(query, 'skip_first_frames', 0.0, 0.0) > 0:
        start_time = parse_float(query, 'skip_first_frames', 0.0, 0.0)/target_rate
        if start_time > 1/modified_rate:
            start_time += 1/modified_rate
    if start_time > 0:
        if start_time > 4:
            post_seek = ['-ss', '4']
            pre_seek = ['-ss', str(start_time - 4)]
        else:
            post_seek = ['-ss', str(start_time)]
            pre_seek = []
    else:
        pre_seek = []
        post_seek = []

    args = [ffmpeg_path, "-v", "error"] + pre_seek + in_args + post_seek
    if target_rate != 0:
        args += ['-r', str(modified_rate)]
    if query.get('force_size','Disabled') != "Disabled":
        size = query['force_size'].split('x')
        if size[0] == '?' or size[1] == '?':
            size[0] = "-2" if size[0] == '?' else f"'min({size[0]},iw)'"
            size[1] = "-2" if size[1] == '?' else f"'min({size[1]},ih)'"
        else:
            #Aspect ratio is likely changed. A more complex command is required
            #to crop the output to the new aspect ratio
            ar = float(size[0])/float(size[1])
            vfilters.append(f"crop=if(gt({ar}\\,a)\\,iw\\,ih*{ar}):if(gt({ar}\\,a)\\,iw/{ar}\\,ih)")
        size = ':'.join(size)
        vfilters.append(f"scale={size}")
    if len(vfilters) > 0:
        args += ["-vf", ",".join(vfilters)]
    frame_cap = parse_int(query, 'frame_load_cap', 0, 0)
    if frame_cap > 0:
        args += ["-frames:v", str(frame_cap)]
    #TODO:reconsider adding high frame cap/setting default frame cap on node
    if query.get('deadline', 'realtime') == 'good':
        deadline = 'good'
    else:
        deadline = 'realtime'

    args += ['-c:v', 'libvpx-vp9','-deadline', deadline, '-cpu-used', '8', '-f', 'webm', '-']

    try:
        debug_log("view_video_preview", filename=filename, args=args)
        proc = await asyncio.create_subprocess_exec(*args, stdout=subprocess.PIPE,
                                                    stdin=subprocess.DEVNULL)
        try:
            resp = web.StreamResponse()
            resp.content_type = 'video/webm'
            resp.headers["Content-Disposition"] = f"filename=\"{filename}\""
            await resp.prepare(request)
            while len(bytes_read := await proc.stdout.read(2**20)) != 0:
                await resp.write(bytes_read)
            #Of dubious value given frequency of kill calls, but more correct
            await proc.wait()
        except (ConnectionResetError, ConnectionError) as e:
            proc.kill()
    except BrokenPipeError as e:
        pass
    return resp
@server.PromptServer.instance.routes.get("/vhs/viewaudio")
async def view_audio(request):
    query = request.rel_url.query
    path_res = await resolve_path(query)
    if isinstance(path_res, web.Response):
        return path_res
    file, filename, output_dir = path_res
    if ffmpeg_path is None:
        #Don't just return file, that provides  arbitrary read access to any file
        if is_safe_path(output_dir, strict=True):
            return web.FileResponse(path=file)
        return error_response(503, "ffmpeg is unavailable and the requested audio preview path is not allowed for direct file serving.")

    in_args = ["-i", file]
    start_time = 0
    if 'start_time' in query:
        start_time = parse_float(query, 'start_time', 0.0, 0.0)
    args = [ffmpeg_path, "-v", "error", '-vn'] + in_args + ['-ss', str(start_time)]
    duration = parse_float(query, 'duration', 0.0, 0.0)
    if duration > 0:
        args += ['-t', str(duration)]
    if query.get('deadline', 'realtime') == 'good':
        deadline = 'good'
    else:
        deadline = 'realtime'

    args += ['-c:a', 'libopus','-deadline', deadline, '-cpu-used', '8', '-f', 'webm', '-']
    try:
        debug_log("view_audio_preview", filename=filename, args=args)
        proc = await asyncio.create_subprocess_exec(*args, stdout=subprocess.PIPE,
                                                    stdin=subprocess.DEVNULL)
        try:
            resp = web.StreamResponse()
            resp.content_type = 'audio/webm'
            resp.headers["Content-Disposition"] = f"filename=\"{filename}\""
            await resp.prepare(request)
            while len(bytes_read := await proc.stdout.read(2**20)) != 0:
                await resp.write(bytes_read)
            #Of dubious value given frequency of kill calls, but more correct
            await proc.wait()
        except (ConnectionResetError, ConnectionError) as e:
            proc.kill()
    except BrokenPipeError as e:
        pass
    return resp

query_cache = {}
@server.PromptServer.instance.routes.get("/vhs/queryvideo")
async def query_video(request):
    query = request.rel_url.query
    filepath = await resolve_path(query)
    #TODO: cache lookup
    if isinstance(filepath, web.Response):
        return filepath
    filepath = filepath[0]
    if filepath.endswith(".webp"):
        # ffmpeg doesn't support decoding animated WebP https://trac.ffmpeg.org/ticket/4907
        return web.json_response({})
    if av is None:
        return web.json_response({"error": "PyAV is not installed; video metadata probing is unavailable."}, status=503)
    if filepath in query_cache and query_cache[filepath][0] == os.stat(filepath).st_mtime:
        source = query_cache[filepath][1]
    else:
        source = {}
        try:
            with av.open(filepath) as cont:
                stream = cont.streams.video[0]
                source['fps'] = float(stream.average_rate)
                source['duration'] = float(cont.duration / av.time_base)

                if stream.codec_context.name == 'vp9':
                    cc = av.Codec('libvpx-vp9', 'r').create()
                else:
                    cc = stream
                def fit():
                    for packet in cont.demux(video=0):
                        yield from cc.decode(packet)
                frame = next(fit())

                source['size'] = [frame.width, frame.height]
                source['alpha'] = 'a' in frame.format.name
                source['frames'] = stream.metadata.get('NUMBER_OF_FRAMES', round(source['duration'] * source['fps']))
                query_cache[filepath] = (os.stat(filepath).st_mtime, source)
        except Exception as exc:
            debug_log("query_video_failed", filepath=filepath, error=str(exc))
    if not 'frames' in source:
        return web.json_response({"error": "Failed to read video metadata."}, status=422)
    loaded = {}
    loaded['duration'] = max(0.0, source['duration'] - parse_float(query, 'start_time', 0.0, 0.0))
    loaded['fps'] = parse_float(query, 'force_rate', 0.0, 0.0) or source.get('fps',1)
    if loaded['fps'] <= 0:
        loaded['fps'] = source.get('fps', 1) or 1
    loaded['duration'] = max(0.0, loaded['duration'] - parse_int(query, 'skip_first_frames', 0, 0) / loaded['fps'])
    loaded['fps'] /= parse_int(query, 'select_every_nth', 1, 1)
    loaded['frames'] = max(0, round(loaded['duration'] * loaded['fps']))
    debug_log("query_video", filepath=filepath, source=source, loaded=loaded)
    return web.json_response({'source': source, 'loaded': loaded})

async def resolve_path(query):
    if "filename" not in query:
        return error_response(400, "Missing required query parameter: filename")
    filename = strip_path(query["filename"])
    if not filename:
        return error_response(400, "Empty filename.")

    #Path code misformats urls on windows and must be skipped
    if is_url(filename):
        try:
            file = await asyncio.to_thread(try_download_video, filename)
        except Exception as exc:
            debug_log("resolve_path_url_failed", source=filename, error=str(exc))
            return error_response(502, f"Failed to download media from URL: {filename}")
        if not file:
            return error_response(502, f"Failed to download media from URL: {filename}")
        output_dir, _ = os.path.split(file)
        debug_log("resolve_path_url", source=filename, resolved=file)
        return file, filename, output_dir
    else:
        filename, output_dir = folder_paths.annotated_filepath(filename)

        type = query.get("type", "output")
        if type == "path":
            #special case for path_based nodes
            #NOTE: output_dir may be empty, but non-None
            output_dir, filename = os.path.split(strip_path(filename))
        if output_dir is None:
            output_dir = folder_paths.get_directory_by_type(type)

        if output_dir is None:
            return error_response(404, f"Unknown media directory type: {type}")

        if not is_safe_path(output_dir):
            return error_response(403, f"Unsafe media directory: {output_dir}")

        if "subfolder" in query:
            output_dir = os.path.join(output_dir, query["subfolder"])

        filename = os.path.basename(filename)
        file = os.path.join(output_dir, filename)

        if not os.path.exists(file):
            return error_response(404, f"Media file not found: {file}")
        if query.get('format', 'video') == 'folder':
            if not os.path.isdir(file):
                return error_response(422, f"Expected a directory for folder preview: {file}")
        else:
            if not os.path.isfile(file) and not validate_sequence(file):
                    return error_response(422, f"Media path is not a file or valid sequence: {file}")
        debug_log("resolve_path_local", filename=filename, output_dir=output_dir, resolved=file, type=type)
        return file, filename, output_dir

@server.PromptServer.instance.routes.get("/vhs/getpath")
@server.PromptServer.instance.routes.get("/getpath")
async def get_path(request):
    query = request.rel_url.query
    if "path" not in query:
        return web.Response(status=204)
    #NOTE: path always ends in `/`, so this is functionally an lstrip
    path = os.path.abspath(strip_path(query["path"]))

    if not os.path.exists(path) or not is_safe_path(path):
        return web.json_response([])

    #Use get so None is default instead of keyerror
    valid_extensions = query.get("extensions")
    if valid_extensions:
        valid_extensions = {
            ext.strip().lower().lstrip(".")
            for ext in valid_extensions.split(",")
            if ext.strip()
        }
    valid_items = []
    for item in os.scandir(path):
        try:
            if item.is_dir():
                valid_items.append(item.name + "/")
                continue
            if valid_extensions is None or item.name.split(".")[-1].lower() in valid_extensions:
                valid_items.append(item.name)
        except OSError:
            #Broken symlinks can throw a very unhelpful "Invalid argument"
            pass
    valid_items.sort(key=lambda f: os.stat(os.path.join(path,f)).st_mtime)
    return web.json_response(valid_items)
