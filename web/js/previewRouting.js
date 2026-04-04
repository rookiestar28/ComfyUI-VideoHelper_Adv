export function shouldUseAdvancedPreview({ advancedPreviews, isInput, format }) {
    if (advancedPreviews === false || advancedPreviews === "Never") {
        return false;
    }
    if (advancedPreviews === true || advancedPreviews === "Always") {
        return true;
    }
    if (advancedPreviews === "Input Only") {
        if (isInput) {
            return true;
        }
        // IMPORTANT: completed output previews must keep using /vhs/viewvideo;
        // raw /view does not reliably render encoded node outputs in ComfyUI.
        return format?.split?.("/")[0] === "video";
    }
    return Boolean(advancedPreviews);
}
