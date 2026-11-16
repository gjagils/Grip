import Foundation
import UIKit

/// Praat met het Grip-backend: POST /api/workouts/parse
struct APIClient {
    var baseURL: String
    var token: String

    enum APIError: LocalizedError {
        case notConfigured
        case server(String)

        var errorDescription: String? {
            switch self {
            case .notConfigured: return "Stel eerst de server-URL in (tandwieltje rechtsboven)."
            case .server(let msg): return msg
            }
        }
    }

    private struct OutMessage: Encodable {
        var role: String
        var text: String
        var images: [String]
    }

    private struct OutBody: Encodable {
        var messages: [OutMessage]
    }

    func parse(history: [ChatMessage]) async throws -> ParseResponse {
        let trimmed = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !trimmed.isEmpty, let url = URL(string: trimmed + "/api/workouts/parse") else {
            throw APIError.notConfigured
        }

        // Alleen de foto's van het laatste gebruikersbericht meesturen; eerdere foto's
        // staan al verwerkt in de assistant-antwoorden. Dat scheelt kosten en tijd.
        let lastUserIndex = history.lastIndex { $0.role == .user }
        var out: [OutMessage] = []
        for (i, m) in history.enumerated() {
            switch m.role {
            case .user:
                let sendImages = i == lastUserIndex
                var text = m.text
                if !sendImages && !m.images.isEmpty {
                    text += text.isEmpty ? "[foto]" : " [foto]"
                }
                out.append(OutMessage(
                    role: "user",
                    text: text,
                    images: sendImages ? m.images.compactMap { $0.uploadBase64() } : []
                ))
            case .assistant:
                out.append(OutMessage(role: "assistant", text: m.raw ?? m.text, images: []))
            case .error:
                continue
            }
        }

        var request = URLRequest(url: url, timeoutInterval: 180)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if !token.isEmpty {
            request.setValue(token, forHTTPHeaderField: "X-Sync-Token")
        }
        request.httpBody = try JSONEncoder().encode(OutBody(messages: out))

        let (data, response) = try await URLSession.shared.data(for: request)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        guard let parsed = try? decoder.decode(ParseResponse.self, from: data) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            throw APIError.server("Onverwacht antwoord van de server (HTTP \(status)).")
        }
        if !parsed.ok {
            throw APIError.server(parsed.error ?? "Onbekende fout")
        }
        return parsed
    }
}

extension UIImage {
    /// Verkleint naar max 1568 px (de grootte waarop Claude afbeeldingen verwerkt) en maakt er base64-JPEG van.
    func uploadBase64(maxDimension: CGFloat = 1568) -> String? {
        let longest = max(size.width, size.height)
        var image = self
        if longest > maxDimension {
            let scale = maxDimension / longest
            let newSize = CGSize(width: size.width * scale, height: size.height * scale)
            let format = UIGraphicsImageRendererFormat.default()
            format.scale = 1
            image = UIGraphicsImageRenderer(size: newSize, format: format).image { _ in
                self.draw(in: CGRect(origin: .zero, size: newSize))
            }
        }
        return image.jpegData(compressionQuality: 0.8)?.base64EncodedString()
    }
}
