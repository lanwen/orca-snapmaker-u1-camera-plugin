// Offline WKWebView CSP test. This is a native bridge model, not an Orca integration test.
import Cocoa
import WebKit

class CSPHost: NSObject, WKNavigationDelegate {
    var view: WKWebView!
    var window: NSWindow!

    func start(_ page: String) throws {
        let config = WKWebViewConfiguration()
        config.userContentController.addUserScript(WKUserScript(source: """
            // This offline active-page harness must not depend on WindowServer occlusion.
            Object.defineProperty(document, 'hidden', {get: () => false});
            window.cspBridgeMessages = [];
            window.orca = {
                onMessage(callback) { window.cspReceive = callback; },
                postMessage(message) { window.cspBridgeMessages.push(message); }
            };
            """, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        view = WKWebView(frame: NSRect(x: 0, y: 0, width: 1100, height: 800), configuration: config)
        view.navigationDelegate = self
        window = NSWindow(contentRect: view.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = view
        window.orderBack(nil)
        view.loadHTMLString(try String(contentsOfFile: page, encoding: .utf8),
                            baseURL: URL(fileURLWithPath: page))
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        webView.evaluateJavaScript("""
            window.cspReceive({type:'target', generation:1, target:'Offline WebKit test'});
            window.cspReceive({generation:1, stats:{state:'paused'}, light:true});
            """) { _, error in
            if let error = error { fputs("Bridge failed: \(error)\n", stderr); exit(1) }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                webView.evaluateJavaScript("""
                    Boolean(document.querySelector('#play') &&
                        document.querySelector('#target').textContent === 'Offline WebKit test' &&
                        document.querySelector('#stats').dataset.state === 'paused' &&
                        window.cspBridgeMessages.some(m => m.type === 'activity') &&
                        window.unapprovedInline !== true)
                    """) { result, error in
                    guard error == nil, result as? Bool == true else {
                        fputs("CSP WebKit failed: \(String(describing: error))\n", stderr); exit(1)
                    }
                    print("CSP WebKit passed")
                    exit(0)
                }
            }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let host = CSPHost()
try host.start(CommandLine.arguments[1])
DispatchQueue.main.asyncAfter(deadline: .now() + 20) { fputs("CSP WebKit timed out\n", stderr); exit(1) }
app.run()
