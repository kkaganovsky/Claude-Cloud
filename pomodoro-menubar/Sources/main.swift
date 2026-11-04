// Pomo — a tiny menu-bar pomodoro timer for macOS 13+.
// Single file, no dependencies. One 1 s timer runs only while a session is
// active; the menu-bar icon is redrawn only when its colour/slice changes.

import Cocoa
import ServiceManagement

private let focusLength: TimeInterval = 25 * 60
private let breakLength: TimeInterval = 5 * 60

enum Phase { case idle, focus, rest }

// MARK: - Colour map (cool → fire). t = fraction of focus time remaining (1 → 0).

private let colourStops: [(t: CGFloat, r: CGFloat, g: CGFloat, b: CGFloat)] = [
    (1.00, 0.35, 0.62, 1.00),   // cool blue
    (0.75, 0.30, 0.82, 0.88),   // teal
    (0.50, 0.98, 0.80, 0.25),   // amber
    (0.25, 1.00, 0.55, 0.20),   // orange
    (0.00, 1.00, 0.23, 0.19),   // red
]

private func fireColour(_ t: CGFloat) -> NSColor {
    let t = min(max(t, 0), 1)
    for i in 0..<(colourStops.count - 1) {
        let a = colourStops[i], b = colourStops[i + 1]
        if t <= a.t && t >= b.t {
            let u = (t - b.t) / (a.t - b.t)
            return NSColor(srgbRed: b.r + (a.r - b.r) * u,
                           green: b.g + (a.g - b.g) * u,
                           blue: b.b + (a.b - b.b) * u, alpha: 1)
        }
    }
    return .systemRed
}

private let breakColour = NSColor(srgbRed: 0.30, green: 0.80, blue: 0.50, alpha: 1)

/// A small pie: faint full circle + solid wedge for the time remaining.
private func pieImage(fraction: CGFloat, colour: NSColor) -> NSImage {
    let size = NSSize(width: 15, height: 15)
    let img = NSImage(size: size, flipped: false) { rect in
        let r = rect.insetBy(dx: 1, dy: 1)
        let ring = NSBezierPath(ovalIn: r)
        ring.lineWidth = 1.2
        colour.withAlphaComponent(0.9).setStroke()
        ring.stroke()
        if fraction > 0 {
            let c = NSPoint(x: r.midX, y: r.midY)
            let wedge = NSBezierPath()
            wedge.move(to: c)
            wedge.appendArc(withCenter: c, radius: r.width / 2 - 1.6,
                            startAngle: 90, endAngle: 90 - 360 * fraction, clockwise: true)
            wedge.close()
            colour.setFill()
            wedge.fill()
        }
        return true
    }
    img.isTemplate = false
    return img
}

// MARK: - Day plan (received from Pomo Plan via pomo://plan?d=<date>&p=<base64url JSON>)

struct PlanItem: Codable {
    var t: String   // task title
    var n: Int      // planned sessions
}

private func todayString() -> String {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.calendar = Calendar(identifier: .gregorian)
    f.dateFormat = "yyyy-MM-dd"
    return f.string(from: Date())
}

/// Strip control characters and cap length — the URL could come from any local app.
private func clean(_ s: String) -> String {
    let scalars = s.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }
    return String(String.UnicodeScalarView(scalars))
        .trimmingCharacters(in: .whitespacesAndNewlines)
        .prefix(100).description
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var phase: Phase = .idle
    private var endDate = Date()
    private var pausedRemaining: TimeInterval?     // non-nil while paused
    private var timer: Timer?
    private var goal = ""
    private var lastIconKey = -1
    private weak var goalField: NSTextField?
    private let defaults = UserDefaults.standard

    private var lastGoal: String? {
        get { defaults.string(forKey: "lastGoal") }
        set { defaults.set(newValue, forKey: "lastGoal") }
    }

    private var sessionsToday: Int {
        get {
            let today = Calendar.current.startOfDay(for: Date()).timeIntervalSince1970
            return defaults.double(forKey: "countDay") == today ? defaults.integer(forKey: "count") : 0
        }
        set {
            defaults.set(Calendar.current.startOfDay(for: Date()).timeIntervalSince1970, forKey: "countDay")
            defaults.set(newValue, forKey: "count")
        }
    }

    // MARK: Plan storage

    private var planItems: [PlanItem] {
        guard defaults.string(forKey: "planDate") == todayString(),
              let data = defaults.data(forKey: "planItems"),
              let items = try? JSONDecoder().decode([PlanItem].self, from: data) else { return [] }
        return items
    }

    /// Sessions completed today per goal title.
    private var planDone: [String: Int] {
        get {
            guard defaults.string(forKey: "planDoneDate") == todayString() else { return [:] }
            return defaults.dictionary(forKey: "planDone") as? [String: Int] ?? [:]
        }
        set {
            defaults.set(todayString(), forKey: "planDoneDate")
            defaults.set(newValue, forKey: "planDone")
        }
    }

    private var nextPlanItem: PlanItem? {
        let done = planDone
        return planItems.first { (done[$0.t] ?? 0) < $0.n }
    }

    func applicationWillFinishLaunching(_ note: Notification) {
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(handleURL(_:reply:)),
            forEventClass: AEEventClass(kInternetEventClass), andEventID: AEEventID(kAEGetURL))
    }

    @objc private func handleURL(_ event: NSAppleEventDescriptor, reply: NSAppleEventDescriptor) {
        guard let str = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue,
              str.count < 20_000,
              let comps = URLComponents(string: str),
              comps.scheme == "pomo", comps.host == "plan",
              let date = comps.queryItems?.first(where: { $0.name == "d" })?.value,
              date == todayString(),
              var b64 = comps.queryItems?.first(where: { $0.name == "p" })?.value else { return }
        b64 = b64.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while b64.count % 4 != 0 { b64 += "=" }
        guard let data = Data(base64Encoded: b64),
              let raw = try? JSONDecoder().decode([PlanItem].self, from: data) else { return }
        let items = raw.prefix(30)
            .map { PlanItem(t: clean($0.t), n: min(max($0.n, 1), 8)) }
            .filter { !$0.t.isEmpty }
        guard let encoded = try? JSONEncoder().encode(items) else { return }
        defaults.set(date, forKey: "planDate")
        defaults.set(encoded, forKey: "planItems")
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        installEditMenu()
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.imagePosition = .imageLeading
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu

        // Start with the Mac: register once on first launch (user can toggle off in the menu).
        if !defaults.bool(forKey: "loginConfigured") {
            try? SMAppService.mainApp.register()
            defaults.set(true, forKey: "loginConfigured")
        }
        render()
    }

    // Without a main menu, ⌘C/⌘V/⌘A don't work in text fields of an agent app.
    private func installEditMenu() {
        let main = NSMenu()
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)
        NSApp.mainMenu = main
    }

    // MARK: Menu (rebuilt only when opened)

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        func add(_ title: String, _ sel: Selector?, _ key: String = "") -> NSMenuItem {
            let item = menu.addItem(withTitle: title, action: sel, keyEquivalent: key)
            item.target = self
            return item
        }

        switch phase {
        case .idle:
            _ = add("Start Focus (25 min)…", #selector(startFocusClicked), "s")
            _ = add("Start Break (5 min)", #selector(startBreakClicked))
        case .focus, .rest:
            let header = menu.addItem(withTitle: phase == .focus ? "🎯 \(goal)" : "☕️ Break", action: nil, keyEquivalent: "")
            header.isEnabled = false
            menu.addItem(.separator())
            _ = add(pausedRemaining == nil ? "Pause" : "Resume", #selector(togglePause), "p")
            _ = add(phase == .focus ? "Finish & Take Break" : "Skip Break", #selector(skipPhase))
            _ = add("Stop", #selector(stopClicked), ".")
        }

        menu.addItem(.separator())
        let items = planItems
        if !items.isEmpty {
            let header = menu.addItem(withTitle: "Today's Plan", action: nil, keyEquivalent: "")
            header.isEnabled = false
            let done = planDone
            for (i, item) in items.enumerated() {
                let d = done[item.t] ?? 0
                let mi = add("\(i + 1). \(item.t)  \(min(d, item.n))/\(item.n) 🍅",
                             phase == .idle ? #selector(startPlanItem(_:)) : nil)
                mi.representedObject = item.t
                mi.state = d >= item.n ? .on : .off
                mi.isEnabled = phase == .idle
            }
        }
        _ = add("Plan My Day…", #selector(openPlanner))
        menu.addItem(.separator())
        let count = menu.addItem(withTitle: "Sessions today: \(sessionsToday)", action: nil, keyEquivalent: "")
        count.isEnabled = false
        let login = add("Launch at Login", #selector(toggleLogin))
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        _ = add("Quit Pomo", #selector(quit), "q")
    }

    // MARK: Actions

    @objc private func startFocusClicked() { askGoalAndStart() }

    @objc private func startPlanItem(_ sender: NSMenuItem) {
        guard let title = sender.representedObject as? String else { return }
        goal = title
        lastGoal = title
        start(.focus)
    }

    @objc private func openPlanner() {
        if let url = URL(string: "pomoplan://open"), !NSWorkspace.shared.open(url) {
            let a = NSAlert()
            a.messageText = "Pomo Plan isn't installed"
            a.informativeText = "Build it from the pomo-plan folder and move it to /Applications."
            NSApp.activate(ignoringOtherApps: true)
            a.runModal()
        }
    }

    @objc private func planPopupChanged(_ sender: NSPopUpButton) {
        if let title = sender.selectedItem?.representedObject as? String {
            goalField?.stringValue = title
        }
    }
    @objc private func startBreakClicked() { start(.rest) }
    @objc private func stopClicked() { stop() }
    @objc private func quit() { NSApp.terminate(nil) }

    @objc private func togglePause() {
        if let rem = pausedRemaining {
            endDate = Date().addingTimeInterval(rem)
            pausedRemaining = nil
            startTicking()
        } else {
            pausedRemaining = max(0, endDate.timeIntervalSinceNow)
            timer?.invalidate(); timer = nil
        }
        lastIconKey = -1
        render()
    }

    @objc private func skipPhase() { phaseEnded(playSound: false) }

    @objc private func toggleLogin() {
        let svc = SMAppService.mainApp
        do {
            if svc.status == .enabled { try svc.unregister() } else { try svc.register() }
        } catch {
            let a = NSAlert()
            a.messageText = "Couldn't change Login Item"
            a.informativeText = "\(error.localizedDescription)\n\nYou can also add Pomo in System Settings → General → Login Items."
            NSApp.activate(ignoringOtherApps: true)
            a.runModal()
        }
    }

    // MARK: Goal prompt

    private func askGoalAndStart() {
        let alert = NSAlert()
        alert.messageText = "What's your goal for this session?"
        alert.informativeText = "25 minutes of focus."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
        field.placeholderString = "e.g. Write the methods section"
        goalField = field

        // With a plan for today: pre-fill the next unfinished task, offer the rest in a pop-up.
        let items = planItems
        if !items.isEmpty {
            let done = planDone
            let popup = NSPopUpButton(frame: NSRect(x: -2, y: 32, width: 304, height: 26), pullsDown: false)
            popup.addItem(withTitle: "Today's plan…")
            for (i, item) in items.enumerated() {
                let d = done[item.t] ?? 0
                popup.addItem(withTitle: "\(i + 1). \(item.t)  (\(min(d, item.n))/\(item.n))")
                popup.lastItem?.representedObject = item.t
            }
            popup.target = self
            popup.action = #selector(planPopupChanged(_:))
            if let next = nextPlanItem {
                field.stringValue = next.t
                if let idx = items.firstIndex(where: { $0.t == next.t }) { popup.selectItem(at: idx + 1) }
            }
            let box = NSView(frame: NSRect(x: 0, y: 0, width: 300, height: 58))
            box.addSubview(popup)
            box.addSubview(field)
            alert.accessoryView = box
            alert.informativeText = "25 minutes of focus. Next up from your plan is filled in."
        } else {
            alert.accessoryView = field
        }
        alert.addButton(withTitle: "Start")

        var reuseIndex: Int? = nil
        if let last = lastGoal, !last.isEmpty {
            let short = last.count > 28 ? String(last.prefix(27)) + "…" : last
            alert.addButton(withTitle: "Reuse “\(short)”")
            reuseIndex = 1
        }
        let cancel = alert.addButton(withTitle: "Cancel")
        cancel.keyEquivalent = "\u{1b}"
        alert.window.initialFirstResponder = field

        NSApp.activate(ignoringOtherApps: true)
        let response = alert.runModal()
        let index = response.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue

        let typed = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if index == 0 {
            goal = typed.isEmpty ? (lastGoal ?? "Focus") : typed
        } else if index == reuseIndex {
            goal = lastGoal ?? "Focus"
        } else {
            return // cancelled
        }
        lastGoal = goal
        start(.focus)
    }

    // MARK: Timer engine

    private func start(_ p: Phase) {
        phase = p
        pausedRemaining = nil
        endDate = Date().addingTimeInterval(p == .focus ? focusLength : breakLength)
        lastIconKey = -1
        startTicking()
        render()
    }

    private func stop() {
        timer?.invalidate(); timer = nil
        phase = .idle
        pausedRemaining = nil
        render()
    }

    private func startTicking() {
        timer?.invalidate()
        let t = Timer(timeInterval: 1, repeats: true) { [weak self] _ in self?.tick() }
        t.tolerance = 0.25            // lets macOS coalesce wake-ups
        RunLoop.main.add(t, forMode: .common)   // keep counting while the menu is open
        timer = t
    }

    private func tick() {
        if endDate.timeIntervalSinceNow <= 0 { phaseEnded(playSound: true) } else { render() }
    }

    private func phaseEnded(playSound: Bool) {
        switch phase {
        case .focus:
            sessionsToday += 1
            var done = planDone
            done[goal, default: 0] += 1
            planDone = done
            if playSound { NSSound(named: "Glass")?.play() }
            start(.rest)
        case .rest:
            if playSound { NSSound(named: "Hero")?.play() }
            stop()
            DispatchQueue.main.async { [weak self] in self?.askGoalAndStart() }
        case .idle:
            break
        }
    }

    // MARK: Drawing

    private func render() {
        guard let button = statusItem.button else { return }

        if phase == .idle {
            lastIconKey = -1
            button.image = NSImage(systemSymbolName: "timer", accessibilityDescription: "Pomo")
            button.image?.isTemplate = true
            button.attributedTitle = NSAttributedString(string: "")
            button.toolTip = "Pomo — click to start a focus session"
            return
        }

        let total = phase == .focus ? focusLength : breakLength
        let remaining = pausedRemaining ?? max(0, endDate.timeIntervalSinceNow)
        let fraction = CGFloat(remaining / total)
        let paused = pausedRemaining != nil

        // Redraw icon only when it would visibly change (~100 steps per session).
        let key = paused ? -2 : Int(fraction * 100)
        if key != lastIconKey {
            lastIconKey = key
            let colour: NSColor = paused ? .secondaryLabelColor
                : (phase == .focus ? fireColour(fraction) : breakColour)
            button.image = pieImage(fraction: fraction, colour: colour)
        }

        let secs = Int(remaining.rounded(.up))
        var text = String(format: " %d:%02d", secs / 60, secs % 60)
        if phase == .rest { text = " Break" + text }
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 13, weight: .medium),
            .foregroundColor: paused ? NSColor.secondaryLabelColor : NSColor.labelColor,
        ]
        button.attributedTitle = NSAttributedString(string: text, attributes: attrs)
        button.toolTip = phase == .focus ? "Goal: \(goal)" : "Break"
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // menu-bar only, no Dock icon
app.run()
