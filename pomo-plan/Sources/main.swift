// Pomo Plan — a morning priority planner that feeds Pomo's focus goals.
// Separate sandboxed app (no network). Idle cost is zero: it only wakes on
// system wake, day change, or one timer set for 5:00 the next morning.
// Hands the ranked plan to Pomo via a local pomo:// URL (Launch Services only).

import Cocoa
import SwiftUI
import ServiceManagement

private let dayStartHour = 5

private func todayString() -> String {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.calendar = Calendar(identifier: .gregorian)
    f.dateFormat = "yyyy-MM-dd"
    return f.string(from: Date())
}

// MARK: - Model

struct PlanTask: Identifiable, Codable, Equatable {
    var id = UUID()
    var title = ""
    var urgent: Double = 5
    var important: Double = 5
    var sessions = 1

    /// Eisenhower-style: importance counts more than urgency.
    var score: Double { important * 0.6 + urgent * 0.4 }

    var quadrant: String {
        switch (important >= 5, urgent >= 5) {
        case (true, true): return "Do first"
        case (true, false): return "Schedule"
        case (false, true): return "Quick / delegate"
        default: return "Maybe drop"
        }
    }

    var quadrantColour: Color {
        switch (important >= 5, urgent >= 5) {
        case (true, true): return .red
        case (true, false): return .blue
        case (false, true): return .orange
        default: return .gray
        }
    }
}

struct CutTask: Identifiable {
    let task: PlanTask
    let reason: String
    var id: UUID { task.id }
}

final class PlanStore: ObservableObject {
    @Published var tasks: [PlanTask] = []
    @Published var ruthless = UserDefaults.standard.bool(forKey: "ruthless")
    @Published var budget = max(1, UserDefaults.standard.object(forKey: "budget") as? Int ?? 6)
    private let key = "tasks"
    static let ruthlessMaxTasks = 3

    init() {
        if let data = UserDefaults.standard.data(forKey: key),
           let saved = try? JSONDecoder().decode([PlanTask].self, from: data) {
            tasks = saved
        }
        if tasks.isEmpty { tasks = [PlanTask()] }
    }

    var ranked: [PlanTask] {
        tasks.filter { !$0.title.trimmingCharacters(in: .whitespaces).isEmpty }
             .sorted { $0.score > $1.score }
    }

    /// The day's plan. Ruthless mode: drop anything not important, keep at most
    /// 3 tasks, and fit everything into the session budget.
    var plan: (kept: [PlanTask], cut: [CutTask]) {
        guard ruthless else { return (ranked, []) }
        var kept: [PlanTask] = [], cut: [CutTask] = []
        var left = budget
        for t in ranked {
            if t.important < 5 {
                cut.append(CutTask(task: t, reason: "not important"))
            } else if kept.count >= Self.ruthlessMaxTasks {
                cut.append(CutTask(task: t, reason: "top \(Self.ruthlessMaxTasks) only"))
            } else if left <= 0 {
                cut.append(CutTask(task: t, reason: "no sessions left"))
            } else {
                var k = t
                k.sessions = min(t.sessions, left)
                left -= k.sessions
                kept.append(k)
            }
        }
        return (kept, cut)
    }

    func save() {
        UserDefaults.standard.set(ruthless, forKey: "ruthless")
        UserDefaults.standard.set(budget, forKey: "budget")
        if let data = try? JSONEncoder().encode(tasks) {
            UserDefaults.standard.set(data, forKey: key)
        }
    }

    func remove(_ id: UUID) {
        // Deferred so SwiftUI isn't mid-render of the row being removed.
        DispatchQueue.main.async { self.tasks.removeAll { $0.id == id } }
    }
}

// MARK: - Views

struct TaskRow: View {
    @Binding var task: PlanTask
    var onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                TextField("Task", text: $task.title)
                    .textFieldStyle(.roundedBorder)
                Stepper("🍅 \(task.sessions)", value: $task.sessions, in: 1...8)
                    .fixedSize()
                    .help("Number of 25-min focus sessions")
                Button(action: onDelete) { Image(systemName: "trash") }
                    .buttonStyle(.borderless)
                    .help("Remove")
            }
            slider("Urgent", $task.urgent)
            slider("Important", $task.important)
            Text(task.quadrant)
                .font(.caption.weight(.semibold))
                .foregroundColor(task.quadrantColour)
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8)
            .fill(Color(nsColor: .controlBackgroundColor)))
    }

    private func slider(_ label: String, _ value: Binding<Double>) -> some View {
        HStack {
            Text(label).frame(width: 72, alignment: .leading).foregroundColor(.secondary)
            Slider(value: value, in: 0...10, step: 1)
            Text("\(Int(value.wrappedValue))").monospacedDigit().frame(width: 22, alignment: .trailing)
        }
    }
}

struct PlanView: View {
    @ObservedObject var store: PlanStore
    var onSend: () -> Void
    var onSkip: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Plan your day").font(.title2.bold())
            Text("Rate each task. Pomo will offer them as focus goals in this order. Unfinished tasks carry over — delete what's done.")
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            ScrollView {
                VStack(spacing: 8) {
                    ForEach($store.tasks) { $task in
                        TaskRow(task: $task) { store.remove(task.id) }
                    }
                }
            }
            .frame(minHeight: 220, maxHeight: 360)

            Button { store.tasks.append(PlanTask()) } label: {
                Label("Add task", systemImage: "plus")
            }

            Divider()
            HStack {
                Toggle(isOn: $store.ruthless) {
                    Label("Ruthless mode", systemImage: "flame.fill")
                }
                .toggleStyle(.switch)
                .help("Cut anything not important, keep the top 3, fit the session budget")
                Spacer()
                if store.ruthless {
                    Stepper("Budget: \(store.budget) 🍅", value: $store.budget, in: 1...16).fixedSize()
                }
            }

            Text("Today's order").font(.headline)
            let plan = store.plan
            let ranked = plan.kept
            if ranked.isEmpty {
                Text(store.tasks.isEmpty || store.ranked.isEmpty ? "Add a task above."
                     : "Ruthless mode cut everything. Raise importance or budget.")
                    .foregroundColor(.secondary)
            }
            ForEach(Array(ranked.enumerated()), id: \.element.id) { i, t in
                HStack {
                    Text("\(i + 1).").monospacedDigit().foregroundColor(.secondary)
                    Text(t.title).lineLimit(1)
                    Spacer()
                    Text(String(repeating: "🍅", count: t.sessions)).font(.caption)
                }
            }
            ForEach(plan.cut) { c in
                HStack {
                    Image(systemName: "xmark").foregroundColor(.secondary)
                    Text(c.task.title).strikethrough().lineLimit(1).foregroundColor(.secondary)
                    Spacer()
                    Text(c.reason).font(.caption).foregroundColor(.secondary)
                }
            }
            let total = ranked.reduce(0) { $0 + $1.sessions }
            if total > 0 {
                Text("\(total) sessions ≈ \(total * 30 / 60) h \(total * 30 % 60) min with breaks")
                    .font(.caption).foregroundColor(.secondary)
            }

            HStack {
                Spacer()
                Button("Skip Today", action: onSkip)
                Button("Send to Pomo", action: onSend)
                    .keyboardShortcut(.defaultAction)
                    .disabled(ranked.isEmpty)
            }
        }
        .padding(20)
        .frame(width: 540)
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let store = PlanStore()
    private var window: NSWindow?
    private var morningTimer: Timer?
    private let defaults = UserDefaults.standard

    func applicationWillFinishLaunching(_ note: Notification) {
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(handleURL(_:reply:)),
            forEventClass: AEEventClass(kInternetEventClass), andEventID: AEEventID(kAEGetURL))
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        installEditMenu()
        if !defaults.bool(forKey: "loginConfigured") {
            try? SMAppService.mainApp.register()
            defaults.set(true, forKey: "loginConfigured")
        }
        let ws = NSWorkspace.shared.notificationCenter
        ws.addObserver(self, selector: #selector(checkDay), name: NSWorkspace.didWakeNotification, object: nil)
        ws.addObserver(self, selector: #selector(checkDay), name: NSWorkspace.screensDidWakeNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(checkDay), name: .NSCalendarDayChanged, object: nil)
        scheduleMorningTimer()
        checkDay()
    }

    // Opening the app again (Finder/Spotlight) shows the planner.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow(); return false
    }

    // pomoplan://open  (sent by Pomo's "Plan My Day…" menu item)
    @objc private func handleURL(_ event: NSAppleEventDescriptor, reply: NSAppleEventDescriptor) {
        showWindow()
    }

    @objc private func checkDay() {
        let hour = Calendar.current.component(.hour, from: Date())
        if hour >= dayStartHour && defaults.string(forKey: "handledDate") != todayString() {
            showWindow()
        }
    }

    private func scheduleMorningTimer() {
        morningTimer?.invalidate()
        let cal = Calendar.current
        guard let next = cal.nextDate(after: Date(), matching: DateComponents(hour: dayStartHour, minute: 0),
                                      matchingPolicy: .nextTime) else { return }
        let t = Timer(fire: next, interval: 0, repeats: false) { [weak self] _ in
            self?.checkDay(); self?.scheduleMorningTimer()
        }
        t.tolerance = 60
        RunLoop.main.add(t, forMode: .common)
        morningTimer = t
    }

    private func showWindow() {
        if window == nil {
            let w = NSWindow(contentRect: .zero, styleMask: [.titled, .closable],
                             backing: .buffered, defer: false)
            w.title = "Pomo Plan"
            w.isReleasedWhenClosed = false
            w.delegate = self
            w.contentViewController = NSHostingController(rootView: PlanView(
                store: store,
                onSend: { [weak self] in self?.send() },
                onSkip: { [weak self] in self?.window?.close() }))
            w.center()
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    // Closing (Skip or ✕) counts as handled for today so it won't nag again.
    func windowWillClose(_ note: Notification) {
        defaults.set(todayString(), forKey: "handledDate")
        store.save()
    }

    private func send() {
        store.save()
        let items = store.plan.kept.prefix(30).map { ["t": String($0.title.prefix(100)), "n": $0.sessions] as [String: Any] }
        guard let json = try? JSONSerialization.data(withJSONObject: Array(items)) else { return }
        let b64 = json.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        var c = URLComponents()
        c.scheme = "pomo"
        c.host = "plan"
        c.queryItems = [URLQueryItem(name: "d", value: todayString()), URLQueryItem(name: "p", value: b64)]
        if let url = c.url, !NSWorkspace.shared.open(url) {
            let a = NSAlert()
            a.messageText = "Couldn't reach Pomo"
            a.informativeText = "Make sure Pomo.app is installed (e.g. in /Applications). Your plan is saved here."
            a.runModal()
            return
        }
        window?.close()
    }

    private func installEditMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Pomo Plan", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)
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
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // no Dock icon; window still comes to front
app.run()
