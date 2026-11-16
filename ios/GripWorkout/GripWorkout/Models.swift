import Foundation
import UIKit

// MARK: - Workout zoals het Grip-backend hem teruggeeft (/api/workouts/parse)
// JSON is snake_case; de decoder gebruikt .convertFromSnakeCase.

struct WorkoutSpec: Codable, Equatable {
    var name: String
    var activity: String      // running | walking | cycling | hiking
    var location: String      // outdoor | indoor
    var warmup: StepSpec?
    var blocks: [BlockSpec]
    var cooldown: StepSpec?
}

struct BlockSpec: Codable, Equatable {
    var iterations: Int
    var steps: [StepSpec]
}

struct StepSpec: Codable, Equatable {
    var purpose: String       // work | recovery
    var goalType: String      // distance | time | open
    var distanceM: Double?
    var durationS: Double?
    var paceFastSPerKm: Double?
    var paceSlowSPerKm: Double?
    var heartRateZone: Int?
    var label: String
}

struct ParseResponse: Decodable {
    var ok: Bool
    var reply: String?
    var workout: WorkoutSpec?
    var raw: String?
    var error: String?
}

// MARK: - Chat

struct ChatMessage: Identifiable {
    enum Role { case user, assistant, error }

    let id = UUID()
    var role: Role
    var text: String
    var images: [UIImage] = []
    var workout: WorkoutSpec? = nil
    /// Het ruwe JSON-antwoord van Claude; gaat mee terug als geschiedenis zodat je kunt bijsturen.
    var raw: String? = nil
}

// MARK: - Weergave-helpers

enum Format {
    static func pace(_ secondsPerKm: Double) -> String {
        let total = Int(secondsPerKm.rounded())
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    static func duration(_ seconds: Double) -> String {
        let total = Int(seconds.rounded())
        if total < 60 { return "\(total) s" }
        if total % 60 == 0 { return "\(total / 60) min" }
        return String(format: "%d:%02d min", total / 60, total % 60)
    }

    static func distance(_ meters: Double) -> String {
        if meters >= 1000 {
            let km = meters / 1000
            return km.truncatingRemainder(dividingBy: 1) == 0
                ? "\(Int(km)) km"
                : String(format: "%.2f km", km).replacingOccurrences(of: ".", with: ",")
        }
        return "\(Int(meters.rounded())) m"
    }

    static func step(_ s: StepSpec) -> String {
        var parts: [String] = []
        switch s.goalType {
        case "distance": if let d = s.distanceM { parts.append(distance(d)) }
        case "time": if let t = s.durationS { parts.append(duration(t)) }
        default: parts.append("open")
        }
        parts.append(s.purpose == "recovery" ? "herstel" : "")
        if let f = s.paceFastSPerKm, let sl = s.paceSlowSPerKm {
            parts.append("@ \(pace(f))–\(pace(sl)) /km")
        }
        if let z = s.heartRateZone { parts.append("zone \(z)") }
        let core = parts.filter { !$0.isEmpty }.joined(separator: " ")
        return s.label.isEmpty ? core : "\(core) · \(s.label)"
    }

    static func activity(_ a: String) -> String {
        switch a {
        case "running": return "Hardlopen"
        case "walking": return "Wandelen"
        case "cycling": return "Fietsen"
        case "hiking": return "Hiken"
        default: return a
        }
    }
}
