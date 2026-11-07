// Standalone WKWebView host for the production camera page and memory experiment.
import Cocoa
import WebKit

func emit(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value) else { return }
    FileHandle.standardOutput.write(data + Data([10]))
}

class Host: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    var view: WKWebView!
    var window: NSWindow!

    func start(page: String, variant: String, mode: String) throws {
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "orca")
        let bridge = """
          (() => {
            let hidden = false, receive, current = null;
            let created = 0, revoked = 0, frames = 0, acknowledgements = 0, loadedFrames = 0;
            const urls = new Set();
            const createURL = URL.createObjectURL.bind(URL);
            const revokeURL = URL.revokeObjectURL.bind(URL);
            URL.createObjectURL = blob => {
              const url = createURL(blob); urls.add(url); created++; return url;
            };
            URL.revokeObjectURL = url => {
              if (urls.delete(url)) revoked++;
              revokeURL(url);
            };
            const release = () => {
              if (current) { URL.revokeObjectURL(current.url); current = null; }
            };
            const send = message => window.webkit.messageHandlers.orca.postMessage(message);
            Object.defineProperty(document, 'hidden', {get: () => hidden});
            Object.defineProperty(document, 'visibilityState', {get: () => hidden ? 'hidden' : 'visible'});
            window.orca = {
              onMessage(callback) { receive = callback; },
              postMessage(message) {
                if (message.type === 'frame') {
                  acknowledgements++;
                  if (current && current.generation === message.generation) release();
                }
                if (message.type === 'activity' && (!message.active || !message.playing)) release();
                send(message);
              }
            };
            window.profileReceive = data => {
              if (data.type === 'target' && current && current.generation !== data.generation) release();
              if (data.image) {
                frames++;
                if ('\(mode)' === 'bridge' && '\(variant)' === 'blob' && !hidden && !document.getElementById('stop').closest('[hidden]')) {
                  release();
                  const comma = data.image.indexOf(',');
                  const bytes = Uint8Array.from(atob(data.image.slice(comma + 1)), c => c.charCodeAt(0));
                  const url = URL.createObjectURL(new Blob([bytes], {type:'image/jpeg'}));
                  current = {url, generation:data.generation};
                  data = {...data, image:url};
                }
              }
              receive(data);
            };
            window.profileAction = action => {
              if (action === 'hide' || action === 'show') {
                hidden = action === 'hide';
                document.dispatchEvent(new Event('visibilitychange'));
              } else {
                const button = document.getElementById(action === 'play' ? 'play' : 'stop');
                if (button.disabled || button.closest('[hidden]')) throw new Error('Camera action unavailable: ' + action);
                button.click();
              }
            };
            window.profileMetrics = () => {
              const image = document.getElementById('image');
              return {hidden, playing:!document.getElementById('stop').closest('[hidden]'),
                canPlay:!document.getElementById('play').disabled,
                sourcePresent:image.hasAttribute('src'), imageVisible:!image.hidden,
                width:image.naturalWidth, height:image.naturalHeight,
                frames, acknowledgements, loadedFrames, created, revoked, liveURLs:urls.size,
                nodes:document.querySelectorAll('*').length};
            };
            window.addEventListener('error', () => send({type:'page_error'}));
            window.addEventListener('unhandledrejection', () => send({type:'page_error'}));
            document.addEventListener('load', event => {
              if (event.target.id === 'image' && event.target.naturalWidth > 0) loadedFrames++;
            }, true);
          })();
        """
        config.userContentController.addUserScript(WKUserScript(
            source: bridge, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        view = WKWebView(frame: NSRect(x: 0, y: 0, width: 1100, height: 800), configuration: config)
        view.isInspectable = true
        view.navigationDelegate = self
        window = NSWindow(contentRect: view.frame, styleMask: [.titled, .closable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "U1 memory profile: " + variant
        window.contentView = view
        window.orderBack(nil)
        view.loadHTMLString(try String(contentsOfFile: page, encoding: .utf8),
                            baseURL: URL(fileURLWithPath: page))
        DispatchQueue.global().async {
            while let line = readLine() {
                // The reader is one long-lived dispatch task; drain Foundation temporaries per message.
                autoreleasepool {
                    guard let data = line.data(using: .utf8),
                          let message = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
                    DispatchQueue.main.async { self.command(message) }
                }
            }
            DispatchQueue.main.async { NSApplication.shared.terminate(nil) }
        }
    }

    func command(_ message: [String: Any]) {
        let kind = message["type"] as? String
        if kind == "quit" { NSApplication.shared.terminate(nil); return }
        var script: String
        if kind == "deliver", let payload = message["message"],
           let data = try? JSONSerialization.data(withJSONObject: payload),
           let json = String(data: data, encoding: .utf8) {
            script = "window.profileReceive(\(json)); null"
        } else if kind == "action", let action = message["action"] as? String,
                  ["play", "stop", "hide", "show"].contains(action) {
            script = "window.profileAction('\(action)'); null"
        } else if kind == "metrics" {
            script = "window.profileMetrics()"
        } else { return }
        view.evaluateJavaScript(script) { value, error in
            if error != nil { emit(["type":"evaluation_error", "command":kind ?? "unknown"]); return }
            if kind == "metrics", var metrics = value as? [String: Any] {
                metrics["type"] = "metrics"
                metrics["id"] = message["id"]
                // Diagnostic SPI gives exact attribution; new-process guessing can sample another app.
                metrics["pid"] = self.view.value(forKey: "_webProcessIdentifier")
                emit(metrics)
            }
        }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        if let body = message.body as? [String: Any] { emit(body) }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { emit(["type":"ready"]) }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { emit(["type":"terminated"]) }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let host = Host()
do {
    guard CommandLine.arguments.count == 4, ["data", "blob"].contains(CommandLine.arguments[2]),
          ["bridge", "player"].contains(CommandLine.arguments[3]) else {
        throw NSError(domain: "Expected PAGE data|blob bridge|player", code: 1)
    }
    try host.start(page: CommandLine.arguments[1], variant: CommandLine.arguments[2], mode: CommandLine.arguments[3])
    app.run()
} catch {
    emit(["type":"startup_error"])
    exit(1)
}
