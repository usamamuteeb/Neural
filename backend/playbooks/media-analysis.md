# Media analysis: understanding a social media post or video from a link

Use this whenever the user shares a link to a video or post (YouTube, Shorts, TikTok, Instagram reels/posts, X/Twitter, Facebook, LinkedIn, Reddit, Vimeo, Twitch clips, or a direct .mp4) and wants to know what it is, what it says, or how to do what it shows.

## 1. Get the content: analyze_media_link first

- Call `analyze_media_link(url)`. It runs locally and free (yt-dlp + captions, or on-device Whisper when there are no captions) and returns: title, uploader, date, duration, stats, the post's description/caption text, a timestamped transcript, and the file paths of a few still frames.
- **Look at the frames**: Read the returned frame .jpg paths (they are images; you can see them). Captions alone miss what's on screen: UI, code, settings menus, slides, product names. For a feature demo, the frames are often where the real answer lives.
- **Zoom in on key moments**: if the transcript says something important happens at a timestamp ("click here", "go to settings", code on screen), call `analyze_media_link(url, frame_times="1:23, 2:05")` again. It reuses the cached download and only grabs those frames.
- Results are cached per video (media/analysis/), so asking again about the same link is instant.
- The full transcript is saved in that folder's analysis.md if the returned one was cut off. Grep or Read a section of it instead of re-running.

## 2. When analyze_media_link can't get it

- **Text or image-only posts** (a tweet with no video, a LinkedIn text post, an Instagram carousel): the tool still returns whatever text and thumbnail it found. If it says there's no media or extraction failed, open the link with the browser tools and read the post. Use browser_find/targeted snapshots per the lean browser rules, and screenshot the image if the picture matters.
- **Login-gated** (Instagram, Facebook, private/age-restricted content): either open it in the browser (it has the user's logins) or, if the user wants this to work through the tool, suggest setting MEDIA_COOKIES_FROM_BROWSER=firefox (or chrome/edge) in .env.
- **"Unsupported URL" / extractor errors / HTTP 403 on a site that normally works**: sites change and yt-dlp updates often. Run `python -m pip install -U yt-dlp` with the backend's venv python (`backend\.venv\Scripts\python.exe` on Windows) once, then retry. If it still fails, fall back to the browser.
- **Not installed**: if the tool says yt-dlp or a Whisper package is missing, give the user the exact pip command it printed. Don't try to install large packages silently mid-conversation unless they say yes.
- **Long videos with no captions** (transcription is slow on CPU: roughly real-time / 3-5x with the base model): for anything over ~15 minutes, offer to run it as a background job so the conversation stays free.

## 3. Explain what's going on

Lead with a short spoken-style summary (2-4 sentences): who posted it, what it's about, the key point. Then, only if useful:
- Main points / steps in order, with timestamps (so the user can jump there).
- Notable claims, numbers, links or tools mentioned.
- Tone/context where it matters (sponsored? a rumor? satire? an official announcement?).
Don't recite the transcript back. Quote at most a short line.

## 4. If it's about a new feature, tool, or technique

This is the high-value case. Go beyond the video:
1. **Pin down exactly what it is**: product, feature name, version, and who made it. Use the frames (UI text, menus, code) plus the description's links.
2. **Verify against official sources**: WebSearch / WebFetch the vendor's docs, changelog, blog or release notes. Videos exaggerate, go out of date, or show beta and region-locked features. Say clearly if the video's claims don't match the docs.
3. **Availability**: is it out now, beta, waitlist, paid-only, plan- or region-restricted, platform-specific (iOS only, desktop only)? Is there a free way? (The user prefers free tools and their Claude subscription, not paid APIs.)
4. **How to do it**: concrete, numbered steps for the user's setup (Windows PC; Python/Node). Include exact menu paths, settings, commands or code. If the video skipped steps, fill them in from the docs.
5. **Feasibility for the user's own projects**: if it's something that could go into one of their projects (for example Neural itself), say plainly whether it's possible, roughly how much work, what it would need (free or paid, any API keys), and any catch. Then offer to build it ("Want me to add this to Neural?"). Don't start changing project code without a go-ahead.
6. If it's not possible or not worth it, say why and suggest the closest thing that does work.

## 5. Etiquette

- Analysis is read-only: no confirmation needed to run analyze_media_link or Read frames.
- Don't download or keep media beyond the analysis cache (auto-pruned after 7 days) unless the user asks to save it. If they want the full video file, that's a separate explicit request.
- Respect the content: summarize, don't republish someone's video or full transcript elsewhere.
