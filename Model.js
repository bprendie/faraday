.pragma library

function stale(info, now) {
    return !info.updated || now / 1000 - info.updated > 15;
}

function visualState(info, now, busy) {
    if (inProgress(info, now, busy)) return "applying";
    if (stale(info, now) || !info.healthy || info.phase === "restoring") return "error";
    return info.mode === "sealed" ? "sealed" : info.mode === "manual-wifi" ? "manual-wifi" : "off";
}

function inProgress(info, now, busy) {
    return busy || (!!info.working && !stale(info, now));
}

function title(state, phase, operation) {
    if (state === "applying") return operation === "restoring" || phase === "restoring" ? "Restoring…" : "Applying…";
    if (phase === "restoring") return "Restore incomplete";
    if (state === "error") return "Needs attention";
    if (state === "sealed") return "Caged";
    if (state === "manual-wifi") return "Selective";
    return "Normal";
}
