import SwiftUI
import WorkoutKit

/// Toont de uitgelezen workout en zet hem in de Workout-app.
struct WorkoutCard: View {
    let spec: WorkoutSpec

    @State private var plan: WorkoutPlan?
    @State private var showPreview = false
    @State private var showSchedule = false
    @State private var errorText: String?
    @State private var scheduledText: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: icon)
                    .foregroundStyle(.orange)
                Text(spec.name).font(.headline)
                Spacer()
                Text("\(Format.activity(spec.activity)) · \(spec.location == "indoor" ? "binnen" : "buiten")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if let warmup = spec.warmup {
                row(title: "Warming-up", detail: Format.step(warmup))
            }
            ForEach(Array(spec.blocks.enumerated()), id: \.offset) { _, block in
                VStack(alignment: .leading, spacing: 4) {
                    Text(block.iterations > 1 ? "\(block.iterations) ×" : "1 ×")
                        .font(.subheadline.bold())
                    ForEach(Array(block.steps.enumerated()), id: \.offset) { _, step in
                        Label(Format.step(step),
                              systemImage: step.purpose == "recovery" ? "figure.walk" : "bolt.fill")
                            .font(.subheadline)
                            .foregroundStyle(step.purpose == "recovery" ? .secondary : .primary)
                    }
                }
                .padding(.leading, 4)
            }
            if let cooldown = spec.cooldown {
                row(title: "Cooling-down", detail: Format.step(cooldown))
            }

            HStack {
                Button {
                    open { showPreview = true }
                } label: {
                    Label("Naar Workout-app", systemImage: "applewatch")
                }
                .buttonStyle(.borderedProminent)
                .tint(.orange)

                Button {
                    open { showSchedule = true }
                } label: {
                    Label("Inplannen", systemImage: "calendar")
                }
                .buttonStyle(.bordered)
            }
            .padding(.top, 4)

            if let scheduledText {
                Label(scheduledText, systemImage: "checkmark.circle.fill")
                    .font(.caption)
                    .foregroundStyle(.green)
            }
            if let errorText {
                Text(errorText).font(.caption).foregroundStyle(.red)
            }
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14))
        .workoutPreview(plan ?? Self.placeholderPlan, isPresented: $showPreview)
        .sheet(isPresented: $showSchedule) {
            if let plan {
                ScheduleSheet(plan: plan) { date in
                    scheduledText = "Ingepland voor \(date.formatted(date: .abbreviated, time: .shortened))"
                }
                .presentationDetents([.medium])
            }
        }
    }

    private var icon: String {
        switch spec.activity {
        case "cycling": return "figure.outdoor.cycle"
        case "walking": return "figure.walk"
        case "hiking": return "figure.hiking"
        default: return "figure.run"
        }
    }

    private func row(title: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.subheadline.bold())
            Text(detail).font(.subheadline)
        }
    }

    private func open(_ action: () -> Void) {
        do {
            plan = try WorkoutBuilder.plan(for: spec)
            errorText = nil
            action()
        } catch {
            errorText = error.localizedDescription
        }
    }

    /// .workoutPreview heeft altijd een plan nodig; dit wordt nooit getoond omdat
    /// showPreview pas true wordt nadat `plan` is gezet.
    private static let placeholderPlan = WorkoutPlan(.custom(CustomWorkout(
        activity: .running, location: .outdoor, displayName: "", blocks: []
    )))
}

/// Plant de workout in; hij verschijnt dan op je Apple Watch in de Workout-app.
private struct ScheduleSheet: View {
    let plan: WorkoutPlan
    var onScheduled: (Date) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var date = Calendar.current.date(byAdding: .day, value: 1, to: .now) ?? .now
    @State private var errorText: String?
    @State private var busy = false

    var body: some View {
        NavigationStack {
            Form {
                DatePicker("Wanneer", selection: $date, in: Date.now..., displayedComponents: [.date, .hourAndMinute])
                if let errorText {
                    Text(errorText).foregroundStyle(.red)
                }
            }
            .navigationTitle("Inplannen")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Annuleer") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Plan in") { Task { await schedule() } }
                        .disabled(busy)
                }
            }
        }
    }

    private func schedule() async {
        busy = true
        defer { busy = false }

        let scheduler = WorkoutScheduler.shared
        var state = await scheduler.authorizationState
        if state != .authorized {
            state = await scheduler.requestAuthorization()
        }
        guard state == .authorized else {
            errorText = "Geen toestemming om workouts in te plannen. Zet het aan via Instellingen > Privacy."
            return
        }

        let components = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: date)
        await scheduler.schedule(plan, at: components)
        onScheduled(date)
        dismiss()
    }
}
