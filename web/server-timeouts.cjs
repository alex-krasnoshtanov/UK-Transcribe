// Preloaded by the container (NODE_OPTIONS=--require ./server-timeouts.cjs).
//
// Next's standalone server keeps Node's default requestTimeout of 5 minutes:
// a request whose body hasn't fully arrived by then is cut off. A lecture of
// ~1 GB over the VPN takes longer than that, and the browser only sees
// "network error during upload". This raises the limit for the servers Next
// creates. UPLOAD_TIMEOUT_S (default 3 h) sets it; 0 turns it off.
// It runs before Next, as plain CommonJS, so require() is the only option.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const http = require("node:http");

const seconds = Number(process.env.UPLOAD_TIMEOUT_S ?? 3 * 60 * 60);
const requestTimeout = Number.isFinite(seconds) && seconds >= 0 ? seconds * 1000 : 3 * 60 * 60 * 1000;

const createServer = http.createServer;
http.createServer = function (options, listener) {
  if (typeof options === "function" || options === undefined) {
    return createServer.call(this, { requestTimeout }, options);
  }
  return createServer.call(this, { requestTimeout, ...options }, listener);
};
