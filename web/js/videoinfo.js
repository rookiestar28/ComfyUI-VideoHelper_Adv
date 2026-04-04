import { app } from '../../../scripts/app.js'
import { parseVideoMetadataBuffer } from './videoMetadataParser.js'


function getVideoMetadata(file) {
    return new Promise((r) => {
        const reader = new FileReader();
        reader.onload = (event) => {
            r(parseVideoMetadataBuffer(event.target.result));
        };

        reader.readAsArrayBuffer(file);
    });
}
function isVideoFile(file) {
    if (file?.name?.endsWith(".webm")) {
        return true;
    }
    if (file?.name?.endsWith(".mp4")) {
        return true;
    }
    if (file?.name?.endsWith(".mkv")) {
        return true;
    }

    return false;
}

let originalHandleFile = app.handleFile;
app.handleFile = handleFile;
let fileInput = document.getElementById("comfy-file-input")
//hijack comfy-file-input to allow webm/mp4/mkv
fileInput.accept += ",video/webm,video/mp4,video/x-matroska";

async function handleFile(file) {
    if (file?.type?.startsWith("video/") || isVideoFile(file)) {
        const videoInfo = await getVideoMetadata(file);
        if (videoInfo?.workflow) {
            await app.loadGraphData(videoInfo.workflow);
            return
        }
    }
    return await originalHandleFile.apply(this, arguments);
}
