import Foundation
import HealthKit
import WorkoutKit

/// Zet een WorkoutSpec (van het backend) om naar een WorkoutKit CustomWorkout.
enum WorkoutBuilder {
    enum BuildError: LocalizedError {
        case noBlocks
        case unsupported(String)

        var errorDescription: String? {
            switch self {
            case .noBlocks: return "De workout bevat geen intervallen."
            case .unsupported(let a): return "Deze activiteit wordt niet ondersteund: \(a)"
            }
        }
    }

    static func plan(for spec: WorkoutSpec) throws -> WorkoutPlan {
        WorkoutPlan(.custom(try customWorkout(for: spec)))
    }

    static func customWorkout(for spec: WorkoutSpec) throws -> CustomWorkout {
        guard !spec.blocks.isEmpty else { throw BuildError.noBlocks }

        let activity = try activityType(spec.activity)
        let location: HKWorkoutSessionLocationType = spec.location == "indoor" ? .indoor : .outdoor

        let blocks = spec.blocks.map { block in
            IntervalBlock(
                steps: block.steps.map { step in
                    IntervalStep(
                        step.purpose == "recovery" ? .recovery : .work,
                        goal: goal(for: step),
                        alert: alert(for: step)
                    )
                },
                iterations: max(1, block.iterations)
            )
        }

        return CustomWorkout(
            activity: activity,
            location: location,
            displayName: spec.name,
            warmup: spec.warmup.map { WorkoutStep(goal: goal(for: $0), alert: alert(for: $0)) },
            blocks: blocks,
            cooldown: spec.cooldown.map { WorkoutStep(goal: goal(for: $0), alert: alert(for: $0)) }
        )
    }

    private static func activityType(_ name: String) throws -> HKWorkoutActivityType {
        switch name {
        case "running": return .running
        case "walking": return .walking
        case "cycling": return .cycling
        case "hiking": return .hiking
        default: throw BuildError.unsupported(name)
        }
    }

    private static func goal(for step: StepSpec) -> WorkoutGoal {
        switch step.goalType {
        case "distance":
            if let m = step.distanceM, m > 0 { return .distance(m, .meters) }
        case "time":
            if let s = step.durationS, s > 0 { return .time(s, .seconds) }
        default:
            break
        }
        return .open
    }

    /// Tempo (s/km) wordt een snelheidsbereik; de Watch toont dat als tempo als je dat zo hebt ingesteld.
    private static func alert(for step: StepSpec) -> (any WorkoutAlert)? {
        if let fast = step.paceFastSPerKm, let slow = step.paceSlowSPerKm, fast > 0, slow > 0 {
            let lower = Measurement(value: 1000.0 / max(fast, slow), unit: UnitSpeed.metersPerSecond)
            let upper = Measurement(value: 1000.0 / min(fast, slow), unit: UnitSpeed.metersPerSecond)
            return SpeedRangeAlert(target: lower...upper, metric: .current)
        }
        if let zone = step.heartRateZone, (1...5).contains(zone) {
            return HeartRateZoneAlert(zone: zone)
        }
        return nil
    }
}
