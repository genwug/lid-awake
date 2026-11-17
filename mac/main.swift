import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow?
    private let port = 8737

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMenu()
        switch ensureAgent() {
        case .ready:
            break
        case .missingPython:
            fail("Не найден python3. Он нужен, чтобы переключать сон.")
            return
        case .missingServer:
            fail("В программе нет server.py.")
            return
        case .failed(let message):
            fail(message)
            return
        }
        showWindow()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    private func setupMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appItem.submenu = appMenu
        appMenu.addItem(
            NSMenuItem(title: "Выйти из «Крышки»", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        )
        NSApp.mainMenu = main
    }

    private enum StartResult {
        case ready
        case missingPython
        case missingServer
        case failed(String)
    }

    private func ensureAgent() -> StartResult {
        guard let python = pythonURL() else { return .missingPython }
        guard let resources = Bundle.main.resourceURL else { return .missingServer }
        let script = resources.appendingPathComponent("server.py")
        guard FileManager.default.fileExists(atPath: script.path) else { return .missingServer }

        let process = Process()
        process.executableURL = python
        process.arguments = [script.path, "--ensure-agent"]
        process.currentDirectoryURL = resources
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        do {
            try process.run()
        } catch {
            return .failed(error.localizedDescription)
        }
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let message = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            return .failed(message?.isEmpty == false ? message! : "Фоновый таймер не запустился")
        }
        return waitUntilUp(timeout: 3) ? .ready : .failed("Страница не открылась. Журнал: ~/Library/Logs/lid-awake.log")
    }

    private func pythonURL() -> URL? {
        let candidates = [
            "/usr/local/bin/python3",
            "/opt/homebrew/bin/python3",
            "/usr/bin/python3",
        ]
        return candidates
            .map { URL(fileURLWithPath: $0) }
            .first { FileManager.default.isExecutableFile(atPath: $0.path) }
    }

    private func waitUntilUp(timeout: TimeInterval) -> Bool {
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/health") else { return false }
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let semaphore = DispatchSemaphore(value: 0)
            var ok = false
            let task = URLSession.shared.dataTask(with: url) { _, response, _ in
                ok = (response as? HTTPURLResponse)?.statusCode == 200
                semaphore.signal()
            }
            task.resume()
            _ = semaphore.wait(timeout: .now() + 0.6)
            if ok { return true }
            Thread.sleep(forTimeInterval: 0.12)
        }
        return false
    }

    private func showWindow() {
        if let window {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        guard let url = URL(string: "http://127.0.0.1:\(port)/") else { return }
        let frame = NSRect(x: 0, y: 0, width: 520, height: 860)
        let window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Крышка"
        window.minSize = NSSize(width: 420, height: 640)
        window.backgroundColor = NSColor(srgbRed: 0.894, green: 0.867, blue: 0.816, alpha: 1)
        window.center()

        let web = WKWebView(frame: window.contentView?.bounds ?? frame)
        web.autoresizingMask = [.width, .height]
        web.setValue(false, forKey: "drawsBackground")
        window.contentView?.addSubview(web)
        web.load(URLRequest(url: url))
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.window = window
    }

    private func fail(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Крышка не запустилась"
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
