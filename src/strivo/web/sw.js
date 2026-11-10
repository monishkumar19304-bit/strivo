// Active only on a secure origin (https or localhost). Caches the app shell; data is network-first.
const C="strivo-v1";
self.addEventListener("install",e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(["/","/pwa/icon-192.png"])));self.skipWaiting()});
self.addEventListener("activate",e=>e.waitUntil(clients.claim()));
self.addEventListener("fetch",e=>{
  const u=new URL(e.request.url);
  if(e.request.method!=="GET"||u.origin!==location.origin)return;
  const netFirst=u.pathname.startsWith("/api/")||u.pathname==="/";
  if(netFirst)e.respondWith(fetch(e.request).then(r=>{const cp=r.clone();caches.open(C).then(c=>c.put(e.request,cp));return r}).catch(()=>caches.match(e.request)));
  else e.respondWith(caches.match(e.request).then(m=>m||fetch(e.request)));
});
