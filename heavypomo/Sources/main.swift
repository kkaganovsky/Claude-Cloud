// HeavyPomo — morning priority planner, multi-day projects and end-of-day
// summary for Pomo. Separate sandboxed app (no network). Idle cost is zero:
// it only wakes on system wake, day change, or two daily one-shot timers.
// Talks to Pomo only through local URLs (Launch Services):
//   HeavyPomo → Pomo:  pomo://plan?d=<date>&p=<base64url JSON>
//   Pomo → HeavyPomo:  heavypomo://open | heavypomo://summary | heavypomo://done?d=&t=&m=

import Cocoa
import SwiftUI
import ServiceManagement

private let dayStartHour = 5
private let wipLimit = 3          // Personal Kanban: start with ~3 active projects
private let maxStepSessionsPerDay = 4

private func dayString(_ date: Date = Date()) -> String {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.calendar = Calendar(identifier: .gregorian)
    f.dateFormat = "yyyy-MM-dd"
    return f.string(from: date)
}

private func clean(_ s: String) -> String {
    let scalars = s.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }
    return String(String(String.UnicodeScalarView(scalars))
        .trimmingCharacters(in: .whitespacesAndNewlines).prefix(100))
}

private func hm(_ minutes: Int) -> String {
    minutes >= 60 ? "\(minutes / 60) h \(minutes % 60) min" : "\(minutes) min"
}

// MARK: - Model

struct PlanTask: Identifiable, Codable, Equatable {
    var id = UUID()
    var title = ""
    var urgent: Double = 5
    var important: Double = 5
    var sessions = 1
    var actual = 0
    var doneDay: String? = nil
    var projectID: UUID? = nil        // set for rows generated from a project

    var done: Bool { doneDay != nil }
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

struct ProjectStep: Identifiable, Codable, Equatable {
    var id = UUID()
    var title = ""
    var estimate = 1                  // 🍅
    var actual = 0                    // 🍅 actually spent (from Pomo)
    var doneDay: String? = nil
    var done: Bool { doneDay != nil }
}

struct Project: Identifiable, Codable, Equatable {
    var id = UUID()
    var name = "New project"
    var important: Double = 7
    var active = true
    var hasDeadline = false
    var deadline = Date().addingTimeInterval(7 * 86_400)
    var steps: [ProjectStep] = [ProjectStep()]

    var nextStep: ProjectStep? { steps.first { !$0.done && !$0.title.trimmingCharacters(in: .whitespaces).isEmpty } }
    var remaining: Int { steps.filter { !$0.done }.reduce(0) { $0 + max(1, $1.estimate - $1.actual) } }
    var totalEstimate: Int { steps.reduce(0) { $0 + $1.estimate } }
    var doneEstimate: Int { steps.filter { $0.done }.reduce(0) { $0 + $1.estimate } }

    func goalTitle(_ step: ProjectStep) -> String { "\(name): \(step.title)" }

    var daysLeft: Int? {
        guard hasDeadline else { return nil }
        let cal = Calendar.current
        return cal.dateComponents([.day], from: cal.startOfDay(for: Date()),
                                  to: cal.startOfDay(for: deadline)).day
    }

    /// 🍅 needed per day to finish by the deadline.
    var perDay: Double? {
        guard let d = daysLeft else { return nil }
        return Double(remaining) / Double(max(1, d))
    }

    /// Urgency rises as the deadline approaches relative to the work left.
    /// 10 = needs this project's full fair share of today's budget (budget / WIP limit).
    func urgency(budget: Int) -> Double {
        guard let d = daysLeft, let need = perDay else { return 3 }
        if d <= 0 { return 10 }
        let share = Double(max(1, budget)) / Double(wipLimit)
        var u = min(10, max(2, 10 * need / share))
        if d <= 2 { u = max(u, 8) }
        return u.rounded()
    }
}

struct CutTask: Identifiable {
    let task: PlanTask
    let reason: String
    var id: UUID { task.id }
}

struct LogEntry: Codable {
    var day: String
    var at: Date
    var title: String
    var minutes: Int
    var project: String?
}

final class PlanStore: ObservableObject {
    @Published var tasks: [PlanTask] = []
    @Published var projects: [Project] = []
    @Published var log: [LogEntry] = []
    @Published var ruthless = UserDefaults.standard.bool(forKey: "ruthless")
    @Published var budget = max(1, UserDefaults.standard.object(forKey: "budget") as? Int ?? 6)
    @Published var autoSummary = UserDefaults.standard.object(forKey: "autoSummary") as? Bool ?? true
    @Published var summaryMinutes = UserDefaults.standard.object(forKey: "summaryMinutes") as? Int ?? (17 * 60 + 30)
    @Published var tab = 0
    var sentPlan: [String: Int] = [:]     // title → planned 🍅, for today
    static let ruthlessMaxTasks = 3
    private let d = UserDefaults.standard

    init() {
        tasks = load("tasks") ?? []
        projects = load("projects") ?? []
        log = load("log") ?? []
        if d.string(forKey: "sentDay") == dayString() { sentPlan = load("sentPlan") ?? [:] }
        if tasks.isEmpty { tasks = [PlanTask()] }
    }

    private func load<T: Decodable>(_ key: String) -> T? {
        guard let data = d.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    private func put<T: Encodable>(_ value: T, _ key: String) {
        if let data = try? JSONEncoder().encode(value) { d.set(data, forKey: key) }
    }

    func save() {
        d.set(ruthless, forKey: "ruthless")
        d.set(budget, forKey: "budget")
        d.set(autoSummary, forKey: "autoSummary")
        d.set(summaryMinutes, forKey: "summaryMinutes")
        put(tasks, "tasks")
        put(projects, "projects")
        put(log, "log")
    }

    // MARK: Daily list

    /// Manual tasks + each active project's next step, best first.
    var ranked: [PlanTask] {
        let manual = tasks.filter { !$0.done && !$0.title.trimmingCharacters(in: .whitespaces).isEmpty }
        let fromProjects: [PlanTask] = projects.filter { $0.active }.compactMap { p in
            guard let s = p.nextStep else { return nil }
            return PlanTask(id: s.id, title: p.goalTitle(s), urgent: p.urgency(budget: budget),
                            important: p.important,
                            sessions: min(maxStepSessionsPerDay, max(1, s.estimate - s.actual)),
                            actual: s.actual, projectID: p.id)
        }
        return (manual + fromProjects).sorted { $0.score > $1.score }
    }

    /// Ruthless mode: drop anything not important, keep at most 3, fit the budget.
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

    func markSent(_ items: [PlanTask]) {
        sentPlan = Dictionary(items.map { ($0.title, $0.sessions) }, uniquingKeysWith: { a, _ in a })
        d.set(dayString(), forKey: "sentDay")
        put(sentPlan, "sentPlan")
    }

    func removeTask(_ id: UUID) {
        DispatchQueue.main.async { self.tasks.removeAll { $0.id == id } }
    }

    func completeTask(_ id: UUID) {
        if let i = tasks.firstIndex(where: { $0.id == id }) { tasks[i].doneDay = dayString() }
    }

    func removeProject(_ id: UUID) {
        DispatchQueue.main.async { self.projects.removeAll { $0.id == id } }
    }

    /// New morning: clear finished one-off tasks (projects keep their history).
    func startNewDay() {
        tasks.removeAll { $0.done }
        if tasks.isEmpty { tasks = [PlanTask()] }
        let cutoff = Date().addingTimeInterval(-60 * 86_400)
        log.removeAll { $0.at < cutoff }
        save()
    }

    // MARK: Session log (from Pomo)

    func record(title: String, minutes: Int) {
        var projectName: String? = nil
        if let i = tasks.firstIndex(where: { $0.title == title && !$0.done }) {
            tasks[i].actual += 1
        } else {
            outer: for p in projects.indices {
                for s in projects[p].steps.indices
                where projects[p].goalTitle(projects[p].steps[s]) == title && !projects[p].steps[s].done {
                    projects[p].steps[s].actual += 1
                    projectName = projects[p].name
                    break outer
                }
            }
        }
        log.append(LogEntry(day: dayString(), at: Date(), title: title, minutes: minutes, project: projectName))
        save()
    }

    // MARK: Weekly-review style checks

    var reviewWarnings: [String] {
        var w: [String] = []
        let active = projects.filter { $0.active }
        if active.count > wipLimit {
            w.append("\(active.count) active projects. Limiting work in progress to about \(wipLimit) helps focus; consider pausing one.")
        }
        for p in active {
            if p.nextStep == nil { w.append("“\(p.name)” has no next step. Add one, or mark it inactive.") }
            if let dl = p.daysLeft, p.remaining > 0 {
                if dl < 0 { w.append("“\(p.name)” is past its deadline with \(p.remaining) 🍅 left.") }
                else if Double(p.remaining) > Double(max(1, dl)) * Double(budget) {
                    w.append("“\(p.name)” needs more sessions than your budget allows before the deadline.")
                }
            }
        }
        return w
    }

    // MARK: End-of-day summary

    func summaryText(for day: String) -> String {
        let entries = log.filter { $0.day == day }
        let total = entries.reduce(0) { $0 + $1.minutes }
        let df = DateFormatter(); df.dateFormat = "EEEE, MMM d"
        var out = "\(df.string(from: Date())) — \(entries.count) session\(entries.count == 1 ? "" : "s"), \(hm(total)) focused\n"

        if entries.isEmpty {
            out += "\nNo focus sessions logged yet today."
        } else {
            var order: [String] = []
            var count: [String: Int] = [:], mins: [String: Int] = [:]
            for e in entries {
                if count[e.title] == nil { order.append(e.title) }
                count[e.title, default: 0] += 1
                mins[e.title, default: 0] += e.minutes
            }
            out += "\nWorked on\n"
            for t in order.sorted(by: { mins[$0]! > mins[$1]! }) {
                var note = ""
                if let planned = sentPlan[t] {
                    let c = count[t]!
                    note = c > planned ? "  [planned \(planned), over]" : c < planned ? "  [planned \(planned)]" : "  [as planned]"
                } else if !sentPlan.isEmpty {
                    note = "  [unplanned]"
                }
                out += "  • \(t): \(count[t]!) 🍅, \(hm(mins[t]!))\(note)\n"
            }
        }

        let doneTasks = tasks.filter { $0.doneDay == day }.map { $0.title }
        let doneSteps = projects.flatMap { p in p.steps.filter { $0.doneDay == day }.map { p.goalTitle($0) } }
        if !(doneTasks + doneSteps).isEmpty {
            out += "\nCompleted\n"
            for t in doneTasks + doneSteps { out += "  ✓ \(t)\n" }
        }

        let worked = Set(entries.map { $0.title })
        let untouched = sentPlan.keys.filter { !worked.contains($0) }.sorted()
        if !untouched.isEmpty {
            out += "\nPlanned but not started\n"
            for t in untouched { out += "  ○ \(t)\n" }
        }

        let active = projects.filter { $0.active }
        if !active.isEmpty {
            out += "\nProjects\n"
            for p in active {
                let today = entries.filter { $0.project == p.name }.count
                var line = "  ▸ \(p.name): \(today) 🍅 today, \(p.remaining) 🍅 left"
                if p.hasDeadline {
                    let ddf = DateFormatter(); ddf.dateFormat = "MMM d"
                    line += ", due \(ddf.string(from: p.deadline))"
                }
                out += line + "\n"
            }
        }

        // Estimate accuracy (Pomodoro practice: compare estimates with actuals).
        let over = projects.flatMap { p in p.steps.filter { $0.actual > $0.estimate }.map { "\(p.goalTitle($0)) (est \($0.estimate), used \($0.actual))" } }
            + tasks.filter { $0.actual > $0.sessions }.map { "\($0.title) (est \($0.sessions), used \($0.actual))" }
        if !over.isEmpty {
            out += "\nOver estimate\n"
            for t in over { out += "  ! \(t)\n" }
        }
        return out
    }

    var summaryTime: Binding<Date> {
        Binding(
            get: { Calendar.current.startOfDay(for: Date()).addingTimeInterval(TimeInterval(self.summaryMinutes * 60)) },
            set: { v in
                let c = Calendar.current.dateComponents([.hour, .minute], from: v)
                self.summaryMinutes = (c.hour ?? 17) * 60 + (c.minute ?? 30)
            })
    }
}

// MARK: - Views

struct TaskRow: View {
    @Binding var task: PlanTask
    var onDone: () -> Void
    var onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Button(action: onDone) { Image(systemName: "circle") }
                    .buttonStyle(.borderless).help("Mark done")
                TextField("Task", text: $task.title).textFieldStyle(.roundedBorder)
                Stepper("🍅 \(task.sessions)", value: $task.sessions, in: 1...8)
                    .fixedSize().help("Estimated 25-min sessions")
                Button(action: onDelete) { Image(systemName: "trash") }
                    .buttonStyle(.borderless).help("Remove")
            }
            slider("Urgent", $task.urgent)
            slider("Important", $task.important)
            HStack {
                Text(task.quadrant).font(.caption.weight(.semibold)).foregroundColor(task.quadrantColour)
                if task.actual > 0 {
                    Text("· used \(task.actual) 🍅").font(.caption).foregroundColor(.secondary)
                }
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color(nsColor: .controlBackgroundColor)))
    }
}

private func slider(_ label: String, _ value: Binding<Double>) -> some View {
    HStack {
        Text(label).frame(width: 72, alignment: .leading).foregroundColor(.secondary)
        Slider(value: value, in: 0...10, step: 1)
        Text("\(Int(value.wrappedValue))").monospacedDigit().frame(width: 22, alignment: .trailing)
    }
}

struct TodayView: View {
    @ObservedObject var store: PlanStore
    var onSend: () -> Void
    var onSkip: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Rate each task. Project next steps join automatically. Pomo will offer them as focus goals in this order.")
                .foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)

            ScrollView {
                VStack(spacing: 8) {
                    ForEach($store.tasks) { $task in
                        if !task.done {
                            TaskRow(task: $task,
                                    onDone: { store.completeTask(task.id) },
                                    onDelete: { store.removeTask(task.id) })
                        }
                    }
                }
            }
            .frame(minHeight: 160, maxHeight: 280)

            Button { store.tasks.append(PlanTask()) } label: { Label("Add task", systemImage: "plus") }

            Divider()
            HStack {
                Toggle(isOn: $store.ruthless) { Label("Ruthless mode", systemImage: "flame.fill") }
                    .toggleStyle(.switch)
                    .help("Cut anything not important, keep the top 3, fit the session budget")
                Spacer()
                Stepper("Sessions today: \(store.budget) 🍅", value: $store.budget, in: 1...16).fixedSize()
            }

            Text("Today's order").font(.headline)
            let plan = store.plan
            let kept = plan.kept
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    if kept.isEmpty {
                        Text(store.ranked.isEmpty ? "Add a task or a project step."
                             : "Ruthless mode cut everything. Raise importance or the budget.")
                            .foregroundColor(.secondary)
                    }
                    ForEach(Array(kept.enumerated()), id: \.element.id) { i, t in
                        HStack {
                            Text("\(i + 1).").monospacedDigit().foregroundColor(.secondary)
                            if t.projectID != nil { Image(systemName: "folder").foregroundColor(.secondary) }
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
                }
            }
            .frame(maxHeight: 150)

            let total = kept.reduce(0) { $0 + $1.sessions }
            HStack {
                if total > 0 {
                    Text("\(total) 🍅 ≈ \(hm(total * 30)) with breaks")
                        .font(.caption)
                        .foregroundColor(total > store.budget ? .orange : .secondary)
                }
                Spacer()
                Button("Skip Today", action: onSkip)
                Button("Send to Pomo", action: onSend)
                    .keyboardShortcut(.defaultAction)
                    .disabled(kept.isEmpty)
            }
        }
    }
}

struct StepRow: View {
    @Binding var step: ProjectStep
    var onDelete: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Button {
                step.doneDay = step.done ? nil : dayString()
            } label: {
                Image(systemName: step.done ? "checkmark.circle.fill" : "circle")
            }
            .buttonStyle(.borderless)
            TextField("Step (1–4 🍅)", text: $step.title)
                .textFieldStyle(.roundedBorder)
                .foregroundColor(step.done ? .secondary : .primary)
            Stepper("est \(step.estimate) 🍅", value: $step.estimate, in: 1...12).fixedSize()
            Text("used \(step.actual)")
                .font(.caption).monospacedDigit()
                .foregroundColor(step.actual > step.estimate ? .orange : .secondary)
                .frame(width: 52, alignment: .leading)
            Button(action: onDelete) { Image(systemName: "minus.circle") }.buttonStyle(.borderless)
        }
    }
}

struct ProjectCard: View {
    @Binding var project: Project
    var budget: Int
    var onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                TextField("Project", text: $project.name)
                    .textFieldStyle(.roundedBorder).font(.headline)
                Toggle("Active", isOn: $project.active).toggleStyle(.switch).fixedSize()
                Button(action: onDelete) { Image(systemName: "trash") }.buttonStyle(.borderless)
            }
            slider("Important", $project.important)
            HStack {
                Toggle("Deadline", isOn: $project.hasDeadline).toggleStyle(.checkbox)
                if project.hasDeadline {
                    DatePicker("", selection: $project.deadline, displayedComponents: .date).labelsHidden()
                }
                Spacer()
                Text(statusLine).font(.caption).foregroundColor(.secondary)
            }
            if project.totalEstimate > 0 {
                ProgressView(value: Double(project.doneEstimate), total: Double(project.totalEstimate))
            }
            ForEach($project.steps) { $step in
                StepRow(step: $step) {
                    let id = step.id
                    DispatchQueue.main.async { project.steps.removeAll { $0.id == id } }
                }
            }
            Button { project.steps.append(ProjectStep()) } label: { Label("Add step", systemImage: "plus") }
                .buttonStyle(.borderless)
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
        .opacity(project.active ? 1 : 0.6)
    }

    private var statusLine: String {
        var s = "\(project.remaining) 🍅 left"
        if let need = project.perDay, let dl = project.daysLeft {
            s += dl < 0 ? " · overdue" : String(format: " · %.1f 🍅/day", need)
            s += " · urgency \(Int(project.urgency(budget: budget)))"
        }
        return s
    }
}

struct ProjectsView: View {
    @ObservedObject var store: PlanStore

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Break each project into steps of 1–4 🍅. Its next unfinished step joins your daily plan, and urgency rises as the deadline nears.")
                    .foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                Spacer()
                Button { store.projects.append(Project()) } label: { Label("New project", systemImage: "plus") }
            }
            ForEach(store.reviewWarnings, id: \.self) { w in
                Label(w, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundColor(.orange)
            }
            ScrollView {
                VStack(spacing: 10) {
                    if store.projects.isEmpty {
                        Text("No projects yet.").foregroundColor(.secondary).padding(.top, 20)
                    }
                    ForEach($store.projects) { $p in
                        ProjectCard(project: $p, budget: store.budget) { store.removeProject(p.id) }
                    }
                }
            }
        }
    }
}

struct SummaryView: View {
    @ObservedObject var store: PlanStore

    var body: some View {
        let text = store.summaryText(for: dayString())
        VStack(alignment: .leading, spacing: 10) {
            ScrollView {
                Text(text)
                    .font(.system(.body, design: .rounded))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Toggle("Show automatically at", isOn: $store.autoSummary).toggleStyle(.checkbox)
                DatePicker("", selection: store.summaryTime, displayedComponents: .hourAndMinute)
                    .labelsHidden().disabled(!store.autoSummary)
                Spacer()
                Button("Copy") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(text, forType: .string)
                }
            }
        }
    }
}

struct RootView: View {
    @ObservedObject var store: PlanStore
    var onSend: () -> Void
    var onSkip: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Picker("", selection: $store.tab) {
                Text("Today").tag(0)
                Text("Projects").tag(1)
                Text("Summary").tag(2)
            }
            .pickerStyle(.segmented).labelsHidden()

            switch store.tab {
            case 1: ProjectsView(store: store)
            case 2: SummaryView(store: store)
            default: TodayView(store: store, onSend: onSend, onSkip: onSkip)
            }
        }
        .padding(20)
        .frame(width: 600, height: 660, alignment: .top)
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let store = PlanStore()
    private var window: NSWindow?
    private var morningTimer: Timer?
    private var summaryTimer: Timer?
    private let defaults = UserDefaults.standard

    func applicationWillFinishLaunching(_ note: Notification) {
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(handleURL(_:reply:)),
            forEventClass: AEEventClass(kInternetEventClass), andEventID: AEEventID(kAEGetURL))
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        installMainMenu()
        // Not registered as a login item by default; opt in via HeavyPomo menu → Launch at Login.
        let ws = NSWorkspace.shared.notificationCenter
        ws.addObserver(self, selector: #selector(checkTime), name: NSWorkspace.didWakeNotification, object: nil)
        ws.addObserver(self, selector: #selector(checkTime), name: NSWorkspace.screensDidWakeNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(checkTime), name: .NSCalendarDayChanged, object: nil)
        scheduleTimers()
        checkTime()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        show(tab: store.tab); return false
    }

    func applicationWillTerminate(_ note: Notification) { store.save() }

    // heavypomo://open | heavypomo://summary | heavypomo://done?d=&t=&m=
    @objc private func handleURL(_ event: NSAppleEventDescriptor, reply: NSAppleEventDescriptor) {
        guard let str = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue,
              str.count < 2_000,
              let c = URLComponents(string: str), c.scheme == "heavypomo" else { return }
        let q = { (name: String) in c.queryItems?.first(where: { $0.name == name })?.value }
        switch c.host {
        case "open": show(tab: 0)
        case "summary": show(tab: 2)
        case "done":
            guard q("d") == dayString(), let t = q("t").map(clean), !t.isEmpty else { return }
            let m = min(max(Int(q("m") ?? "25") ?? 25, 0), 60)
            store.record(title: t, minutes: m)
        default: break
        }
    }

    @objc private func checkTime() {
        let now = Date()
        let cal = Calendar.current
        let minutes = cal.component(.hour, from: now) * 60 + cal.component(.minute, from: now)
        let today = dayString()

        if minutes >= dayStartHour * 60 && defaults.string(forKey: "handledDate") != today {
            store.startNewDay()
            show(tab: 0)
        } else if store.autoSummary && minutes >= store.summaryMinutes
                    && defaults.string(forKey: "summaryShown") != today
                    && store.log.contains(where: { $0.day == today }) {
            defaults.set(today, forKey: "summaryShown")
            show(tab: 2)
        }
    }

    private func oneShot(at minutesOfDay: Int, _ action: @escaping () -> Void) -> Timer? {
        guard let next = Calendar.current.nextDate(
            after: Date(), matching: DateComponents(hour: minutesOfDay / 60, minute: minutesOfDay % 60),
            matchingPolicy: .nextTime) else { return nil }
        let t = Timer(fire: next, interval: 0, repeats: false) { _ in action() }
        t.tolerance = 60
        RunLoop.main.add(t, forMode: .common)
        return t
    }

    private func scheduleTimers() {
        morningTimer?.invalidate()
        summaryTimer?.invalidate()
        morningTimer = oneShot(at: dayStartHour * 60) { [weak self] in self?.checkTime(); self?.scheduleTimers() }
        summaryTimer = oneShot(at: store.summaryMinutes) { [weak self] in self?.checkTime(); self?.scheduleTimers() }
    }

    private func show(tab: Int) {
        store.tab = tab
        if window == nil {
            let w = NSWindow(contentRect: .zero, styleMask: [.titled, .closable, .miniaturizable],
                             backing: .buffered, defer: false)
            w.title = "HeavyPomo"
            w.isReleasedWhenClosed = false
            w.delegate = self
            w.contentViewController = NSHostingController(rootView: RootView(
                store: store,
                onSend: { [weak self] in self?.send() },
                onSkip: { [weak self] in self?.window?.close() }))
            w.center()
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    // Closing counts as "planned" for today so the morning prompt won't nag again.
    func windowWillClose(_ note: Notification) {
        defaults.set(dayString(), forKey: "handledDate")
        store.save()
        scheduleTimers()      // pick up a changed summary time
    }

    private func send() {
        store.save()
        let kept = Array(store.plan.kept.prefix(30))
        let items = kept.map { ["t": String($0.title.prefix(100)), "n": $0.sessions] as [String: Any] }
        guard let json = try? JSONSerialization.data(withJSONObject: items) else { return }
        let b64 = json.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        var c = URLComponents()
        c.scheme = "pomo"
        c.host = "plan"
        c.queryItems = [URLQueryItem(name: "d", value: dayString()), URLQueryItem(name: "p", value: b64)]
        guard let url = c.url, NSWorkspace.shared.open(url) else {
            let a = NSAlert()
            a.messageText = "Couldn't reach Pomo"
            a.informativeText = "Make sure Pomo.app is installed (e.g. in /Applications). Your plan is saved here."
            a.runModal()
            return
        }
        store.markSent(kept)
        window?.close()
    }

    @objc private func toggleLogin(_ sender: NSMenuItem) {
        let svc = SMAppService.mainApp
        do {
            if svc.status == .enabled { try svc.unregister() } else { try svc.register() }
        } catch {
            let a = NSAlert()
            a.messageText = "Couldn't change Login Item"
            a.informativeText = error.localizedDescription
            a.runModal()
        }
        sender.state = svc.status == .enabled ? .on : .off
    }

    private func installMainMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        let login = appMenu.addItem(withTitle: "Launch at Login", action: #selector(toggleLogin(_:)), keyEquivalent: "")
        login.target = self
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit HeavyPomo", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
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
        let windowItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowItem.submenu = windowMenu
        main.addItem(windowItem)
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // no Dock icon; window still comes to front
app.run()
