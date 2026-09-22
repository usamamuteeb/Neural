// Neural service worker.
//
// Its only real job is Web Push: receive a push event while the PWA
// isn't in the foreground, turn it into an OS notification, and focus or
// open the app when the notification is tapped. It deliberately does
// NOT cache the app shell -- a previous cache-first version with a
// static cache name once pinned the browser to a stale UI forever, immune
// to server-side changes and even Cache-Control headers (service worker
// interception happens before HTTP caching semantics apply at all). If
// offline support gets added later it needs a versioned cache name and a
// network-first fetch handler -- never cache-first on the shell.

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "Neural", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "Neural";
  const options = {
    body: data.body || "",
    icon: "icon-192.png",
    badge: "icon-192.png",
    tag: data.tag || undefined,
    renotify: !!data.tag,
    data: { url: data.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    (async () => {
      const allClients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of allClients) {
        if ("focus" in client) {
          client.focus();
          return;
        }
      }
      if (self.clients.openWindow) {
        await self.clients.openWindow(targetUrl);
      }
    })()
  );
});
