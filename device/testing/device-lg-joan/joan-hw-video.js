// Decode video on the Venus block through FFmpeg's V4L2 path. Firefox leaves
// this off on V4L2-only hardware unless forced; the temp/ffmpeg patches in
// this repository supply the timebase and DRM PRIME pieces it needs.
pref("media.hardware-video-decoding.force-enabled", true);
