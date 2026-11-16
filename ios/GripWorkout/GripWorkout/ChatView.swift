import PhotosUI
import SwiftUI

@MainActor
@Observable
final class ChatViewModel {
    var messages: [ChatMessage] = []
    var input = ""
    var pendingImages: [UIImage] = []
    var isLoading = false

    func send(baseURL: String, token: String) async {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty || !pendingImages.isEmpty, !isLoading else { return }

        messages.append(ChatMessage(role: .user, text: text, images: pendingImages))
        input = ""
        pendingImages = []
        isLoading = true
        defer { isLoading = false }

        do {
            let result = try await APIClient(baseURL: baseURL, token: token).parse(history: messages)
            messages.append(ChatMessage(
                role: .assistant,
                text: result.reply ?? "",
                workout: result.workout,
                raw: result.raw
            ))
        } catch {
            messages.append(ChatMessage(role: .error, text: error.localizedDescription))
        }
    }

    func reset() {
        messages = []
        pendingImages = []
        input = ""
    }
}

struct ChatView: View {
    @AppStorage("serverURL") private var serverURL = ""
    @AppStorage("syncToken") private var syncToken = ""

    @State private var model = ChatViewModel()
    @State private var photoItems: [PhotosPickerItem] = []
    @State private var showCamera = false
    @State private var showSettings = false
    @FocusState private var inputFocused: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                messageList
                Divider()
                composer
            }
            .navigationTitle("Workout Chat")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Nieuw", systemImage: "square.and.pencil") { model.reset() }
                        .disabled(model.messages.isEmpty)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Instellingen", systemImage: "gearshape") { showSettings = true }
                }
            }
            .sheet(isPresented: $showSettings) { SettingsView() }
            .fullScreenCover(isPresented: $showCamera) {
                CameraPicker { image in model.pendingImages.append(image) }
                    .ignoresSafeArea()
            }
            .onChange(of: photoItems) { _, items in
                Task { await loadPhotos(items) }
            }
            .onAppear {
                if serverURL.isEmpty { showSettings = true }
            }
        }
    }

    // MARK: Berichten

    private var messageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if model.messages.isEmpty {
                        emptyState
                    }
                    ForEach(model.messages) { message in
                        MessageRow(message: message)
                            .id(message.id)
                    }
                    if model.isLoading {
                        HStack(spacing: 8) {
                            ProgressView()
                            Text("Workout maken…").foregroundStyle(.secondary)
                        }
                        .id("loading")
                    }
                }
                .padding()
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: model.messages.count) {
                guard let last = model.messages.last?.id else { return }
                withAnimation { proxy.scrollTo(last, anchor: .bottom) }
            }
            .onChange(of: model.isLoading) { _, loading in
                if loading { withAnimation { proxy.scrollTo("loading", anchor: .bottom) } }
            }
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Beschrijf je training of stuur een foto van je schema.")
                .font(.headline)
            Text("Bijvoorbeeld:")
                .foregroundStyle(.secondary)
            ForEach([
                "3 x 400 m rennen op 4:30/km, 2 min wandelen ertussen",
                "10 min inlopen, 6 x 1 km op 5:00 met 90 s dribbel, 10 min uitlopen",
                "45 min rustig fietsen in zone 2",
            ], id: \.self) { example in
                Button {
                    model.input = example
                    inputFocused = true
                } label: {
                    Text(example)
                        .font(.subheadline)
                        .multilineTextAlignment(.leading)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.top, 24)
    }

    // MARK: Invoer

    private var composer: some View {
        VStack(spacing: 8) {
            if !model.pendingImages.isEmpty {
                ScrollView(.horizontal) {
                    HStack {
                        ForEach(Array(model.pendingImages.enumerated()), id: \.offset) { index, image in
                            Image(uiImage: image)
                                .resizable()
                                .scaledToFill()
                                .frame(width: 64, height: 64)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                                .overlay(alignment: .topTrailing) {
                                    Button {
                                        model.pendingImages.remove(at: index)
                                    } label: {
                                        Image(systemName: "xmark.circle.fill")
                                            .symbolRenderingMode(.palette)
                                            .foregroundStyle(.white, .black.opacity(0.6))
                                    }
                                    .padding(2)
                                }
                        }
                    }
                }
            }

            HStack(alignment: .bottom, spacing: 8) {
                Button {
                    showCamera = true
                } label: {
                    Image(systemName: "camera").font(.title3)
                }
                .disabled(!UIImagePickerController.isSourceTypeAvailable(.camera))

                PhotosPicker(selection: $photoItems, maxSelectionCount: 5, matching: .images) {
                    Image(systemName: "photo.on.rectangle").font(.title3)
                }

                TextField("Beschrijf je workout…", text: $model.input, axis: .vertical)
                    .lineLimit(1...5)
                    .textFieldStyle(.roundedBorder)
                    .focused($inputFocused)

                Button {
                    Task { await model.send(baseURL: serverURL, token: syncToken) }
                } label: {
                    Image(systemName: "arrow.up.circle.fill").font(.title)
                }
                .disabled(model.isLoading
                          || (model.input.trimmingCharacters(in: .whitespaces).isEmpty
                              && model.pendingImages.isEmpty))
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
    }

    private func loadPhotos(_ items: [PhotosPickerItem]) async {
        guard !items.isEmpty else { return }
        for item in items {
            if let data = try? await item.loadTransferable(type: Data.self),
               let image = UIImage(data: data) {
                model.pendingImages.append(image)
            }
        }
        photoItems = []
    }
}

// MARK: - Eén bericht

private struct MessageRow: View {
    let message: ChatMessage

    var body: some View {
        switch message.role {
        case .user:
            VStack(alignment: .trailing, spacing: 6) {
                if !message.images.isEmpty {
                    HStack {
                        ForEach(Array(message.images.enumerated()), id: \.offset) { _, image in
                            Image(uiImage: image)
                                .resizable()
                                .scaledToFill()
                                .frame(width: 90, height: 90)
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                    }
                }
                if !message.text.isEmpty {
                    Text(message.text)
                        .padding(10)
                        .background(Color.accentColor.opacity(0.15), in: RoundedRectangle(cornerRadius: 14))
                }
            }
            .frame(maxWidth: .infinity, alignment: .trailing)

        case .assistant:
            VStack(alignment: .leading, spacing: 8) {
                if !message.text.isEmpty {
                    Text(message.text)
                }
                if let workout = message.workout {
                    WorkoutCard(spec: workout)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

        case .error:
            Label(message.text, systemImage: "exclamationmark.triangle")
                .foregroundStyle(.red)
                .font(.subheadline)
        }
    }
}

// MARK: - Camera

struct CameraPicker: UIViewControllerRepresentable {
    var onImage: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let parent: CameraPicker
        init(_ parent: CameraPicker) { self.parent = parent }

        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let image = info[.originalImage] as? UIImage {
                parent.onImage(image)
            }
            parent.dismiss()
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            parent.dismiss()
        }
    }
}
