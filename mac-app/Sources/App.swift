// Unicorn Studio - paint, animate and send displays to a Pimoroni Cosmic / Galactic / Stellar Unicorn over local MQTT.
import SwiftUI
import AppKit
import UniformTypeIdentifiers

enum Tool: String, CaseIterable { case pencil, eraser, fill, picker, text }

func col(_ c: UInt32) -> Color {
    Color(red: Double((c >> 16) & 255) / 255, green: Double((c >> 8) & 255) / 255, blue: Double(c & 255) / 255)
}
func u32(_ c: Color) -> UInt32 {
    let n = NSColor(c).usingColorSpace(.sRGB) ?? .black
    return UInt32(max(0, min(255, n.redComponent * 255 + 0.5))) << 16 | UInt32(max(0, min(255, n.greenComponent * 255 + 0.5))) << 8
        | UInt32(max(0, min(255, n.blueComponent * 255 + 0.5)))
}

struct BoardSeen: Identifiable, Hashable { let id: String; let board: String; let w: Int; let h: Int; let version: String }

final class AppModel: ObservableObject {
    // canvas
    @Published var W = 32
    @Published var H = 32
    @Published var autoSize = true                 // follow the connected board's size
    @Published var fitMode: FitMode = .fit
    @Published var frames: [Frame] = [Frame()] { didSet { liveChanged() } }
    @Published var cur = 0 { didSet { liveChanged() } }
    @Published var name = "untitled"
    @Published var desc = ""
    @Published var fileURL: URL?
    @Published var color: UInt32 = 0xFF7800
    @Published var tool: Tool = .pencil
    @Published var stampText = "HI"
    @Published var playing = false
    @Published var live = false { didSet { if !live && oldValue { up?.liveOff() } else { liveChanged() } } }
    @Published var library: [URL] = []
    @Published var conn = "not configured"
    @Published var connected = false
    @Published var sendLog = ""
    @Published var sendOK: Bool? = nil
    @Published var sending = false
    @Published var onion = false
    @Published var cfg = ConnConfig()
    @Published var showSettings = false
    @Published var seen: [BoardSeen] = []
    // board state (from BASE/state)
    @Published var bOn = true
    @Published var bBright = 50.0
    @Published var bMode = "clock"
    @Published var bRot = "0"
    @Published var bRots = ["0", "90", "180", "270"]
    @Published var bText = ""
    @Published var bSpeed = 10.0
    @Published var bCustoms: [String] = []
    @Published var bPlaylists: [String] = []
    @Published var bPlaying: String? = nil
    @Published var bModes: [String] = []
    @Published var bW = 0
    @Published var bH = 0
    @Published var bBoard = ""
    @Published var bVersion = ""
    @Published var bInfo = ""
    @Published var bStateAt = Date.distantPast
    @Published var refreshing = false
    // playlists
    @Published var plNames: [String] = []
    @Published var pl = Playlist(name: "my_playlist", items: [])
    @Published var plDirty = false
    @Published var libraryLoading = false
    @Published var plUnsent = false      // local edits not yet sent to the board
    var plResult = ""
    var undoStack: [[Frame]] = []
    var recent: [UInt32] = []
    let mq = MQTT()
    var base: String { cfg.base }
    var up: Uploader?
    var liveT = Date.distantPast
    var livePending = false
    var playTimer: Timer?
    var playT = Date()

    let palette: [UInt32] = [0xFFFFFF, 0x000000, 0xFF0000, 0xFF7800, 0xFFD200, 0x00FF00, 0x00C8A0, 0x00B4FF,
                             0x0040FF, 0x8000FF, 0xFF00C8, 0xFF8CB4, 0x804020, 0x808080, 0x303030, 0x40FF80]

    init() {
        // The library lives in ~/Documents, which macOS may guard with a permission prompt: read it off the main
        // thread so the window and the board connection never wait for that answer.
        libraryLoading = true
        DispatchQueue.global(qos: .userInitiated).async {
            DisplayIO.seedLibraryIfEmpty()
            let lib = DisplayIO.list(), pls = PlaylistIO.list()
            let first = pls.first.flatMap { PlaylistIO.load($0) }
            DispatchQueue.main.async {
                self.library = lib; self.plNames = pls; self.libraryLoading = false
                if self.pl.items.isEmpty, let p = first { self.pl = p }
            }
        }
        if let s = UserDefaults.standard.string(forKey: "canvas"), let k = BoardKind.all.first(where: { $0.id == s }) { W = k.w; H = k.h }
        autoSize = UserDefaults.standard.object(forKey: "autoSize") as? Bool ?? true
        frames = [Frame(w: W, h: H)]
        cfg = Config.load()
        mq.onConnected = { [weak self] ok, why in DispatchQueue.main.async {
            guard let s = self else { return }
            s.connected = ok; s.conn = ok ? "connected" : "offline: " + why
            if ok { s.refreshBoard() }                    // auto-refresh on launch and on every reconnect
        } }
        mq.onMessage = { [weak self] t, p in
            guard let self = self else { return }
            let b = self.base
            if t.hasPrefix(b + "/custom/") { self.up?.feed(t, p) }
            DispatchQueue.main.async {
                if t == b + "/state" { self.applyState(p); if t.hasPrefix("unicorn/") { self.noteSeen(t, p) } }
                else if t == b + "/status" { self.bInfo = "board " + String(decoding: p.prefix(20), as: UTF8.self) }
                else if t == b + "/playlist/result" { self.plResult = String(decoding: p.prefix(200), as: UTF8.self) }
                else if t.hasPrefix("unicorn/"), t.hasSuffix("/state") { self.noteSeen(t, p) }
            }
        }
        connect()
    }

    func connect() {
        mq.stop()
        guard cfg.complete else { conn = "open Settings to enter the MQTT broker and login"; connected = false; return }
        var subs = ["unicorn/+/state"]
        if !base.isEmpty {
            up = Uploader(mq: mq, base: base)
            subs += [base + "/state", base + "/status", base + "/custom/ack", base + "/custom/result", base + "/custom/result2", base + "/playlist/result"]
        }
        conn = "connecting…"
        mq.start(cfg, subs: subs)
    }
    func applySettings(_ c: ConnConfig) { cfg = c; Config.save(c); resetBoardState(); connect() }
    func resetBoardState() { bCustoms = []; bPlaylists = []; bPlaying = nil; bModes = []; bW = 0; bH = 0; bInfo = ""; bVersion = "" }

    func noteSeen(_ t: String, _ p: Data) {
        let id = String(t.dropFirst("unicorn/".count).dropLast("/state".count))
        guard !id.isEmpty, !id.contains("/"), let j = try? JSONSerialization.jsonObject(with: p) as? [String: Any] else { return }
        let s = BoardSeen(id: "unicorn/" + id, board: j["board"] as? String ?? "cosmic", w: j["width"] as? Int ?? 32, h: j["height"] as? Int ?? 32,
                          version: j["version"] as? String ?? "?")
        seen.removeAll { $0.id == s.id }; seen.append(s); seen.sort { $0.id < $1.id }
        if base.isEmpty, seen.count == 1 { cfg.base = s.id; Config.save(cfg); connect() }   // first board found: use it
    }

    func applyState(_ p: Data) {
        guard let j = try? JSONSerialization.jsonObject(with: p) as? [String: Any] else { return }
        bOn = (j["state"] as? String) == "ON"
        if let b = j["brightness"] as? Int { bBright = Double(b) * 100 / 255 }
        bMode = j["mode"] as? String ?? bMode
        bRot = j["rotation"] as? String ?? bRot
        bText = j["text"] as? String ?? bText
        if let sp = j["speed"] as? Int { bSpeed = Double(sp) }
        bCustoms = (j["customs"] as? [String] ?? bCustoms).sorted()
        bPlaylists = (j["playlists"] as? [String] ?? []).sorted()
        bPlaying = j["playing"] as? String
        bModes = j["modes"] as? [String] ?? []
        bVersion = j["version"] as? String ?? "?"
        bW = j["width"] as? Int ?? 32; bH = j["height"] as? Int ?? 32
        bBoard = j["board"] as? String ?? (bW == 32 ? "cosmic" : "")
        bRots = bW == bH ? ["0", "90", "180", "270"] : ["0", "180"]
        up?.v2 = bVersion.hasPrefix("us-")
        var info = "\(bBoard.capitalized) \(bW)×\(bH) · fw \(bVersion) · up \((j["uptime"] as? Int ?? 0) / 60) min"
        if let r = j["reset_reason"] as? String, !r.isEmpty { info += " · last reset: " + r }
        bInfo = info
        bStateAt = Date(); refreshing = false
        if autoSize, bW > 0, bH > 0, (bW != W || bH != H) { setCanvas(bW, bH) }
    }
    /// What is playing right now ("custom:x", "clock", ...), also inside a playlist.
    var nowPlaying: String { bPlaying ?? bMode }
    var modes: [String] {
        var m = bModes.isEmpty ? BUILTIN_MODES : bModes.filter { !$0.hasPrefix("custom:") && !$0.hasPrefix("playlist:") }
        m += bCustoms.map { "custom:" + $0 } + bPlaylists.map { "playlist:" + $0 }
        if !m.contains(bMode) { m.append(bMode) }
        return m
    }
    var builtins: [String] {
        bModes.isEmpty ? BUILTIN_MODES : bModes.filter { !$0.hasPrefix("custom:") && !$0.hasPrefix("playlist:") }
    }
    func cmd(_ k: String, _ v: String) { guard !base.isEmpty else { return }; mq.publish(base + "/" + k + "/set", v) }
    func light(_ d: [String: Any]) { if let j = try? JSONSerialization.data(withJSONObject: d) { mq.publish(base + "/light/set", j) } }
    /// Ask the board to publish its state (and the list of displays/playlists on it) now.
    func refreshBoard() {
        guard connected, !base.isEmpty else { return }
        refreshing = true
        cmd("refresh", "1")
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            guard let s = self, s.refreshing else { return }
            s.refreshing = false
            if Date().timeIntervalSince(s.bStateAt) > 5 { s.bInfo = "no answer from the board (offline, or older firmware)" }
        }
    }
    func play(_ mode: String) { cmd("mode", mode); bMode = mode; DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { self.refreshBoard() } }
    func deleteFromBoard(_ name: String) {
        mq.publish(base + "/custom/delete", name); sendLog = "asked the board to delete \(name)"
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { self.refreshBoard() }
    }
    func deletePlaylistFromBoard(_ name: String) {
        mq.publish(base + "/playlist/delete", name)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { self.refreshBoard() }
    }

    // ---- canvas size ----
    func setCanvas(_ w: Int, _ h: Int) {
        guard w != W || h != H else { return }
        let d = Display(name: name, desc: desc, w: W, h: H, frames: frames).resized(w, h, mode: fitMode)
        stopPlay(); snapshot()
        W = w; H = h; frames = d.frames; cur = min(cur, frames.count - 1)
        UserDefaults.standard.set(BoardKind.forSize(w, h).id, forKey: "canvas")
    }

    // ---- editing ----
    var px: [UInt32] { frames[cur].px }
    func snapshot() { undoStack.append(frames); if undoStack.count > 60 { undoStack.removeFirst() } }
    func undo() { if let f = undoStack.popLast(), f.first?.px.count == W * H { frames = f; cur = min(cur, frames.count - 1) } }
    func set(_ x: Int, _ y: Int, _ c: UInt32) {
        guard x >= 0, y >= 0, x < W, y < H else { return }
        if frames[cur].px[y * W + x] != c { frames[cur].px[y * W + x] = c }
    }
    func line(_ a: (Int, Int), _ b: (Int, Int), _ c: UInt32) {
        var (x0, y0) = a; let (x1, y1) = b
        let dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1, dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1
        var err = dx + dy
        while true {
            set(x0, y0, c)
            if x0 == x1 && y0 == y1 { break }
            let e2 = 2 * err
            if e2 >= dy { err += dy; x0 += sx }
            if e2 <= dx { err += dx; y0 += sy }
        }
    }
    func fill(_ x: Int, _ y: Int, _ c: UInt32) {
        var p = frames[cur].px
        let t = p[y * W + x]; if t == c { return }
        var st = [(x, y)]
        while let (cx, cy) = st.popLast() {
            if cx < 0 || cy < 0 || cx >= W || cy >= H || p[cy * W + cx] != t { continue }
            p[cy * W + cx] = c
            st += [(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)]
        }
        frames[cur].px = p
    }
    func stamp(_ x: Int, _ y: Int) {
        var p = frames[cur].px
        for (dx, dy) in PixelFont.layout(stampText) {
            let X = x + dx, Y = y + dy
            if X >= 0, Y >= 0, X < W, Y < H { p[Y * W + X] = color }
        }
        frames[cur].px = p
    }
    func useColor(_ c: UInt32) {
        color = c
        recent.removeAll { $0 == c }; recent.insert(c, at: 0); if recent.count > 8 { recent.removeLast() }
    }

    // ---- frames ----
    func addFrame() { snapshot(); frames.insert(Frame(w: W, h: H, dur: frames[cur].dur), at: cur + 1); cur += 1 }
    func dupFrame() { snapshot(); var f = frames[cur]; f.id = UUID(); frames.insert(f, at: cur + 1); cur += 1 }
    func delFrame() { guard frames.count > 1 else { return }; snapshot(); frames.remove(at: cur); cur = min(cur, frames.count - 1) }
    func moveFrame(_ d: Int) {
        let n = cur + d; guard n >= 0, n < frames.count else { return }
        snapshot(); frames.swapAt(cur, n); cur = n
    }
    func clearFrame() { snapshot(); frames[cur].px = [UInt32](repeating: 0, count: W * H) }
    func shift(_ dx: Int, _ dy: Int) {
        snapshot(); var p = [UInt32](repeating: 0, count: W * H)
        for y in 0..<H { for x in 0..<W { p[((y + dy + H) % H) * W + (x + dx + W) % W] = frames[cur].px[y * W + x] } }
        frames[cur].px = p
    }
    func flipH() { snapshot(); var p = frames[cur].px; for y in 0..<H { for x in 0..<W { p[y * W + x] = frames[cur].px[y * W + W - 1 - x] } }; frames[cur].px = p }
    func togglePlay() {
        playing.toggle(); playTimer?.invalidate(); playTimer = nil
        guard playing else { return }
        playT = Date()
        playTimer = Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { [weak self] _ in
            guard let s = self else { return }
            if Date().timeIntervalSince(s.playT) * 1000 >= Double(s.frames[s.cur].dur) {
                s.playT = Date(); s.cur = (s.cur + 1) % s.frames.count
            }
        }
    }

    // ---- live mirror (throttled) ----
    var boardSized: Bool { bW == 0 || (bW == W && bH == H) }
    func liveChanged() {
        guard live, connected, boardSized, let up = up else { return }
        let gap = Date().timeIntervalSince(liveT)
        if gap >= 0.25 { liveT = Date(); up.live(frames[cur]) }
        else if !livePending {
            livePending = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25 - gap) { [weak self] in
                guard let s = self else { return }
                s.livePending = false; s.liveT = Date()
                if s.live && s.boardSized { s.up?.live(s.frames[s.cur]) }
            }
        }
    }

    // ---- files ----
    func refreshLibrary() { library = DisplayIO.list() }
    func newDisplay() { snapshot(); frames = [Frame(w: W, h: H)]; cur = 0; name = "untitled"; desc = ""; fileURL = nil }
    func open(_ u: URL) {
        do {
            var d = try DisplayIO.load(u)
            let native = "\(d.w)×\(d.h)"
            if d.w != W || d.h != H { d = d.resized(W, H, mode: fitMode) }
            stopPlay(); snapshot()
            frames = d.frames; cur = 0; name = d.name; desc = d.desc; fileURL = u
            sendLog = "opened " + DisplayIO.rel(u) + " (\(d.frames.count) frames" + (native != "\(W)×\(H)" ? ", \(native) → \(W)×\(H) \(fitMode.rawValue))" : ")")
            sendOK = nil
        } catch { sendLog = "open failed: \(error.localizedDescription)"; sendOK = false }
    }
    func stopPlay() { if playing { togglePlay() } }
    func save() {
        let u = fileURL ?? DisplayIO.libDir.appendingPathComponent(DisplayIO.boardName(name) + ".json")
        do { try DisplayIO.save(Display(name: name, desc: desc, w: W, h: H, frames: frames), to: u); fileURL = u; refreshLibrary(); sendLog = "saved " + DisplayIO.rel(u) }
        catch { sendLog = "save failed: \(error.localizedDescription)" }
    }
    func saveAs() {
        let p = NSSavePanel(); p.allowedContentTypes = [.json]; p.directoryURL = DisplayIO.libDir
        p.nameFieldStringValue = DisplayIO.boardName(name) + ".json"
        if p.runModal() == .OK, let u = p.url { fileURL = u; save() }
    }
    func importFiles() {
        let p = NSOpenPanel(); p.allowsMultipleSelection = true
        p.allowedContentTypes = [.gif, .png, .jpeg, .json]
        guard p.runModal() == .OK else { return }
        var last: URL?; var n = 0; var errs = [String]()
        for u in p.urls {
            do {
                let d = u.pathExtension.lowercased() == "json" ? try DisplayIO.load(u) : try DisplayIO.importImage(u, w: W, h: H)
                let dst = DisplayIO.libDir.appendingPathComponent("Imported/" + DisplayIO.boardName(d.name) + ".json")
                try DisplayIO.save(d, to: dst); last = dst; n += 1
            } catch { errs.append(u.lastPathComponent + ": " + error.localizedDescription) }
        }
        refreshLibrary()
        if let l = last { open(l) }
        sendLog = "imported \(n) file(s) into Imported/" + (errs.isEmpty ? "" : " · errors: " + errs.joined(separator: "; "))
    }
    func trash(_ u: URL) { try? FileManager.default.trashItem(at: u, resultingItemURL: nil); refreshLibrary() }

    /// Board-sized copy of a display.
    func forBoard(_ d: Display) -> Display { bW > 0 ? d.resized(bW, bH, mode: fitMode) : d }

    func sendToBoard(_ d: Display? = nil) {
        guard !sending else { sendLog = "an upload is already running"; return }
        guard let up = up, connected else { sendLog = "not connected to MQTT"; sendOK = false; return }
        let disp = forBoard(d ?? Display(name: name, desc: desc, w: W, h: H, frames: frames))
        let n = DisplayIO.boardName(disp.name)
        sending = true; sendOK = nil; sendLog = "sending \(n)…"
        if live { live = false }
        DispatchQueue.global().async {
            let r = up.send(name: n, frames: disp.frames) { i, t in DispatchQueue.main.async { self.sendLog = "sending \(n): frame \(i)/\(t) confirmed" } }
            DispatchQueue.main.async {
                self.sending = false; self.sendOK = r.ok
                self.sendLog = (r.ok ? "✓ " : "✗ Not saved: ") + r.msg + (disp.frames.count > MAX_FRAMES ? " (board keeps the first \(MAX_FRAMES) frames)" : "")
                self.refreshBoard()
            }
        }
    }

    // ---- playlists ----
    func refreshPlaylists() { plNames = PlaylistIO.list() }
    func newPlaylist() {
        var n = "playlist", i = 1
        while plNames.contains(n) { i += 1; n = "playlist_\(i)" }
        pl = Playlist(name: n, items: []); savePlaylist()
    }
    func selectPlaylist(_ n: String) { if let p = PlaylistIO.load(n) { pl = p; plDirty = false; plUnsent = !bPlaylists.contains(n) } }
    func savePlaylist() {
        pl.name = DisplayIO.boardName(pl.name)
        do { try PlaylistIO.save(pl); plDirty = false; plUnsent = true; refreshPlaylists() } catch { sendLog = "playlist save failed: \(error.localizedDescription)" }
    }
    func renamePlaylist(_ new: String) {
        let n = DisplayIO.boardName(new); guard n != pl.name, !n.isEmpty else { return }
        let old = pl.name
        pl.name = n; savePlaylist(); PlaylistIO.delete(old); refreshPlaylists()
        if bPlaylists.contains(old) { deletePlaylistFromBoard(old) }
    }
    func deletePlaylist() {
        let old = pl.name
        PlaylistIO.delete(old); refreshPlaylists()
        if bPlaylists.contains(old) { deletePlaylistFromBoard(old) }
        if let f = plNames.first, let p = PlaylistIO.load(f) { pl = p } else { pl = Playlist(name: "my_playlist", items: []) }
    }
    func addToPlaylist(mode: String, file: URL? = nil) {
        pl.items.append(PlaylistItem(mode: mode, seconds: 10, file: file.map { DisplayIO.rel($0) })); plDirty = true; savePlaylist()
    }
    /// Add a display that is already on the board (from the "On the board" list) to the open playlist.
    func addBoardItem(_ mode: String) {
        let n = mode.hasPrefix("custom:") ? String(mode.dropFirst(7)) : ""
        let u = n.isEmpty ? nil : library.first { DisplayIO.boardName($0.deletingPathExtension().lastPathComponent) == n }
        addToPlaylist(mode: mode, file: u)
        sendLog = "added \(n.isEmpty ? mode : n) to playlist “\(pl.name)”; press Send to update the board"; sendOK = nil
    }
    func removePlaylistItems(_ ids: Set<UUID>) {
        guard !ids.isEmpty else { return }
        pl.items.removeAll { ids.contains($0.id) }; savePlaylist()
    }
    func addLibraryItem(_ u: URL) {
        let n = (try? DisplayIO.load(u)).map { DisplayIO.boardName($0.name) } ?? DisplayIO.boardName(u.deletingPathExtension().lastPathComponent)
        addToPlaylist(mode: "custom:" + n, file: u)
    }
    /// Upload any custom displays the board lacks (without switching to them), store the playlist on the board, start it.
    func activatePlaylist(play doPlay: Bool = true) {
        guard !sending else { sendLog = "an upload is already running"; return }
        guard let up = up, connected else { sendLog = "not connected to MQTT"; sendOK = false; return }
        guard up.v2 else { sendLog = "playlists need the us-2 firmware on the board"; sendOK = false; return }
        guard !pl.items.isEmpty else { sendLog = "the playlist is empty"; return }
        savePlaylist()
        let p = pl, have = Set(bCustoms)
        var todo = [(String, URL)]()
        for it in p.items where it.mode.hasPrefix("custom:") {
            let n = String(it.mode.dropFirst(7))
            if have.contains(n) || todo.contains(where: { $0.0 == n }) { continue }
            let u = it.file.map { DisplayIO.libDir.appendingPathComponent($0) } ?? library.first { DisplayIO.boardName($0.deletingPathExtension().lastPathComponent) == n }
            if let u = u { todo.append((n, u)) }
        }
        sending = true; sendOK = nil
        DispatchQueue.global().async {
            var errs = [String]()
            for (i, (n, u)) in todo.enumerated() {
                DispatchQueue.main.async { self.sendLog = "playlist: uploading \(n) (\(i + 1)/\(todo.count))…" }
                guard let d0 = try? DisplayIO.load(u) else { errs.append("\(n): cannot read \(DisplayIO.rel(u))"); continue }
                let d = DispatchQueue.main.sync { self.forBoard(d0) }
                let r = up.send(name: n, frames: d.frames, play: false) { _, _ in }
                if !r.ok { errs.append("\(n): \(r.msg)") }
            }
            // store it on the board and wait for the board's own answer (never assume success)
            DispatchQueue.main.sync { self.plResult = ""; self.mq.publish(self.base + "/playlist/save", PlaylistIO.boardJSON(p)) }
            var ans = ""
            for _ in 0..<60 {
                Thread.sleep(forTimeInterval: 0.1)
                ans = DispatchQueue.main.sync { self.plResult }
                if !ans.isEmpty { break }
            }
            DispatchQueue.main.async {
                self.sending = false
                if !ans.hasPrefix("ok playlist \(p.name)") {
                    self.sendOK = false
                    self.sendLog = ans.isEmpty ? "the board did not confirm the playlist (no answer in 6 s); nothing changed"
                                               : "board: " + ans
                    self.refreshBoard(); return
                }
                self.plUnsent = false
                if doPlay { self.play("playlist:" + p.name) } else { self.refreshBoard() }
                self.sendOK = errs.isEmpty
                let what = doPlay ? "saved on the board and started" : "saved on the board"
                self.sendLog = errs.isEmpty ? "✓ playlist \(p.name) \(what) (\(p.items.count) items)"
                    : "playlist \(p.name) \(what), but some items were not uploaded (they will be skipped): " + errs.joined(separator: "; ")
            }
        }
    }
}

// ---------- views ----------
struct PixelGrid: View {
    let px: [UInt32]; let w: Int; let h: Int; var ghost: [UInt32]? = nil; var grid = true
    var body: some View {
        Canvas { ctx, size in
            let c = min(size.width / CGFloat(w), size.height / CGFloat(h))
            let ox = (size.width - c * CGFloat(w)) / 2, oy = (size.height - c * CGFloat(h)) / 2
            ctx.fill(Path(CGRect(origin: .zero, size: size)), with: .color(Color(white: 0.05)))
            guard px.count == w * h else { return }
            for i in 0..<(w * h) {
                let r = CGRect(x: ox + CGFloat(i % w) * c, y: oy + CGFloat(i / w) * c, width: c, height: c)
                if px[i] != 0 { ctx.fill(Path(r), with: .color(col(px[i]))) }
                else if let g = ghost, g.count == px.count, g[i] != 0 { ctx.fill(Path(r), with: .color(col(g[i]).opacity(0.25))) }
            }
            if grid && c > 6 {
                var p = Path()
                for k in 0...w { p.move(to: CGPoint(x: ox + CGFloat(k) * c, y: oy)); p.addLine(to: CGPoint(x: ox + CGFloat(k) * c, y: oy + CGFloat(h) * c)) }
                for k in 0...h { p.move(to: CGPoint(x: ox, y: oy + CGFloat(k) * c)); p.addLine(to: CGPoint(x: ox + CGFloat(w) * c, y: oy + CGFloat(k) * c)) }
                ctx.stroke(p, with: .color(.white.opacity(0.07)), lineWidth: 0.5)
            }
        }
    }
}

struct EditorCanvas: View {
    @ObservedObject var m: AppModel
    @State var last: (Int, Int)? = nil
    let box: CGFloat = 544
    var cell: CGFloat { min(box / CGFloat(m.W), box / CGFloat(m.H)) }
    var body: some View {
        PixelGrid(px: m.px, w: m.W, h: m.H, ghost: m.onion && m.cur > 0 ? m.frames[m.cur - 1].px : nil)
            .frame(width: cell * CGFloat(m.W), height: cell * CGFloat(m.H))
            .border(Color.gray.opacity(0.5))
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { v in
                    let x = Int(v.location.x / cell), y = Int(v.location.y / cell)
                    guard x >= 0, y >= 0, x < m.W, y < m.H else { return }
                    let first = last == nil
                    if first && m.tool != .picker { m.snapshot() }
                    switch m.tool {
                    case .pencil, .eraser:
                        let c: UInt32 = m.tool == .eraser ? 0 : m.color
                        if let l = last { m.line(l, (x, y), c) } else { m.set(x, y, c) }
                    case .fill: if first { m.fill(x, y, m.color) }
                    case .picker: m.useColor(m.px[y * m.W + x])
                    case .text: if first { m.stamp(x, y) }
                    }
                    last = (x, y)
                }
                .onEnded { _ in last = nil })
            .frame(width: box, height: box)
    }
}

struct SettingsView: View {
    @ObservedObject var m: AppModel
    @State var c = ConnConfig()
    @State var port = "1883"
    @Environment(\.dismiss) var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Connection").font(.title2)
            Form {
                TextField("MQTT broker (host or IP)", text: $c.host)
                TextField("Port", text: $port)
                Toggle("TLS (port 8883 on most brokers)", isOn: $c.tls)
                TextField("User", text: $c.user)
                SecureField("Password (stored in your Keychain)", text: $c.pass)
                TextField("Board topic (unicorn/xxxxxx)", text: $c.base)
                if !m.seen.isEmpty {
                    Picker("Boards seen", selection: $c.base) {
                        ForEach(m.seen) { s in Text("\(s.id) · \(s.board) \(s.w)×\(s.h) · \(s.version)").tag(s.id) }
                    }
                }
            }
            Text("Leave the board topic empty to use the first board that reports in.").font(.caption).foregroundColor(.secondary)
            HStack { Spacer(); Button("Cancel") { dismiss() }
                Button("Save & connect") { c.port = UInt16(port) ?? 1883; m.applySettings(c); dismiss() }.buttonStyle(.borderedProminent) }
        }.padding(20).frame(width: 460)
        .onAppear { c = m.cfg; port = String(m.cfg.port) }
    }
}

struct ContentView: View {
    @ObservedObject var m: AppModel
    @State var filter = ""
    @State var tab = 0
    @State var confirmDelete: String? = nil
    @State var renaming = ""
    @State var addBuiltin = "clock"
    @State var plSel = Set<UUID>()
    var body: some View {
        HSplitView {
            library.frame(minWidth: 210, idealWidth: 240, maxWidth: 320)
            VStack(alignment: .leading, spacing: 8) {
                toolbar
                HStack(alignment: .top, spacing: 12) {
                    EditorCanvas(m: m)
                    paletteView
                }
                framesStrip
                Text(m.sendLog).font(.caption).foregroundColor(m.sendOK == false ? .red : (m.sendOK == true ? .green : .secondary)).lineLimit(3)
            }.padding(10).frame(minWidth: 760)
            VStack(spacing: 0) {
                Picker("", selection: $tab) { Text("Board").tag(0); Text("Playlists").tag(1) }.pickerStyle(.segmented).padding(6)
                if tab == 0 { controls } else { playlists }
            }.frame(minWidth: 270, idealWidth: 290, maxWidth: 360)
        }
        .sheet(isPresented: $m.showSettings) { SettingsView(m: m) }
        .alert("Delete \(confirmDelete ?? "") from the board?", isPresented: Binding(get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } })) {
            Button("Delete", role: .destructive) { if let c = confirmDelete { m.deleteFromBoard(c) }; confirmDelete = nil }
            Button("Cancel", role: .cancel) { confirmDelete = nil }
        } message: { Text("The file in your library is kept.") }
    }

    var library: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Library").font(.headline)
            if m.libraryLoading { Text("loading the library… (allow access to Documents if macOS asks)").font(.caption2).foregroundColor(.secondary) }
            TextField("Filter", text: $filter).textFieldStyle(.roundedBorder)
            List(m.library.filter { filter.isEmpty || DisplayIO.rel($0).localizedCaseInsensitiveContains(filter) }, id: \.self) { u in
                Text(DisplayIO.rel(u).replacingOccurrences(of: ".json", with: ""))
                    .font(.system(size: 12)).fontWeight(u == m.fileURL ? .bold : .regular)
                    .onTapGesture(count: 2) { m.open(u); m.sendToBoard() }
                    .onTapGesture { m.open(u) }
                    .contextMenu {
                        Button("Open") { m.open(u) }
                        Button("Send to Unicorn") { m.open(u); m.sendToBoard() }
                        Button("Add to playlist “\(m.pl.name)”") { m.addLibraryItem(u) }
                        Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([u]) }
                        Divider()
                        Button("Move to Trash") { m.trash(u) }
                    }
            }
            HStack {
                Button("Refresh") { m.refreshLibrary() }
                Button("Folder") { NSWorkspace.shared.open(DisplayIO.libDir) }
            }
            Text("Click to open · double-click to send · right-click to add to a playlist").font(.caption2).foregroundColor(.secondary)
        }.padding(8)
    }

    var toolbar: some View {
        HStack(spacing: 8) {
            Picker("", selection: $m.tool) {
                Text("✏️ Pencil").tag(Tool.pencil); Text("🧽 Erase").tag(Tool.eraser); Text("🪣 Fill").tag(Tool.fill)
                Text("💧 Pick").tag(Tool.picker); Text("🔤 Text").tag(Tool.text)
            }.pickerStyle(.segmented).frame(width: 360)
            if m.tool == .text { TextField("text", text: $m.stampText).frame(width: 80).textFieldStyle(.roundedBorder) }
            Spacer()
            Menu("Canvas \(m.W)×\(m.H)") {
                Toggle("Match the connected board", isOn: Binding(get: { m.autoSize }, set: {
                    m.autoSize = $0; UserDefaults.standard.set($0, forKey: "autoSize"); if $0, m.bW > 0 { m.setCanvas(m.bW, m.bH) } }))
                Divider()
                ForEach(BoardKind.all) { k in Button(k.label) { m.autoSize = false; UserDefaults.standard.set(false, forKey: "autoSize"); m.setCanvas(k.w, k.h) } }
                Divider()
                Picker("Resize by", selection: $m.fitMode) {
                    Text("Fit (letterbox)").tag(FitMode.fit); Text("Fill (crop)").tag(FitMode.fill); Text("Stretch").tag(FitMode.stretch)
                }
            }.frame(width: 140)
            Button("Undo") { m.undo() }.keyboardShortcut("z")
            Button("New") { m.newDisplay() }.keyboardShortcut("n")
            Button("Import…") { m.importFiles() }.keyboardShortcut("i")
            Button("Save") { m.save() }.keyboardShortcut("s")
            Button("Save As…") { m.saveAs() }
        }
    }

    var paletteView: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack { Text("Name"); TextField("name", text: $m.name).textFieldStyle(.roundedBorder).frame(width: 140) }
            ColorPicker("Color", selection: Binding(get: { col(m.color) }, set: { m.useColor(u32($0)) }), supportsOpacity: false)
            LazyVGrid(columns: Array(repeating: GridItem(.fixed(26), spacing: 4), count: 4), spacing: 4) {
                ForEach(m.palette, id: \.self) { c in swatch(c) }
            }
            if !m.recent.isEmpty {
                Text("Recent").font(.caption)
                LazyVGrid(columns: Array(repeating: GridItem(.fixed(26), spacing: 4), count: 4), spacing: 4) {
                    ForEach(m.recent, id: \.self) { c in swatch(c) }
                }
            }
            Divider()
            Text("Frame tools").font(.caption)
            HStack { Button("←") { m.shift(-1, 0) }; Button("→") { m.shift(1, 0) }; Button("↑") { m.shift(0, -1) }; Button("↓") { m.shift(0, 1) } }
            HStack { Button("Flip") { m.flipH() }; Button("Clear") { m.clearFrame() } }
            Toggle("Onion skin", isOn: $m.onion)
            Divider()
            Button(m.sending ? "Sending…" : "Send to Unicorn") { m.sendToBoard() }
                .keyboardShortcut(.return).disabled(m.sending || !m.connected).buttonStyle(.borderedProminent)
            Toggle("Live (mirror canvas)", isOn: $m.live).disabled(!m.connected || !m.boardSized)
            Text(m.boardSized ? "Upright here; the board applies its rotation." : "Canvas differs from the board (\(m.bW)×\(m.bH)); sending resizes it.")
                .font(.caption2).foregroundColor(.secondary).frame(width: 170, alignment: .leading)
        }.frame(width: 180)
    }
    func swatch(_ c: UInt32) -> some View {
        RoundedRectangle(cornerRadius: 3).fill(col(c)).frame(width: 26, height: 26)
            .overlay(RoundedRectangle(cornerRadius: 3).stroke(c == m.color ? Color.white : Color.gray.opacity(0.4), lineWidth: c == m.color ? 2 : 1))
            .onTapGesture { m.useColor(c) }
    }

    var framesStrip: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text("Frames \(m.cur + 1)/\(m.frames.count)").font(.headline)
                Button("+ Add") { m.addFrame() }; Button("Duplicate") { m.dupFrame() }; Button("Delete") { m.delFrame() }
                Button("◀") { m.moveFrame(-1) }; Button("▶") { m.moveFrame(1) }
                Button(m.playing ? "■ Stop" : "▶︎ Play") { m.togglePlay() }.keyboardShortcut(" ", modifiers: [])
                Text("ms")
                TextField("ms", value: Binding(get: { m.frames[m.cur].dur }, set: { m.frames[m.cur].dur = max(20, min(60000, $0)) }), formatter: NumberFormatter())
                    .frame(width: 60).textFieldStyle(.roundedBorder)
                Button("all") { let d = m.frames[m.cur].dur; m.snapshot(); for i in m.frames.indices { m.frames[i].dur = d } }
                if m.frames.count > MAX_FRAMES { Text("board plays first \(MAX_FRAMES)").foregroundColor(.orange).font(.caption) }
            }
            ScrollView(.horizontal) {
                HStack(spacing: 4) {
                    ForEach(Array(m.frames.enumerated()), id: \.element.id) { i, f in
                        VStack(spacing: 1) {
                            PixelGrid(px: f.px, w: m.W, h: m.H, grid: false).frame(width: 56, height: 56)
                                .border(i == m.cur ? Color.accentColor : Color.gray.opacity(0.3), width: i == m.cur ? 2 : 1)
                            Text("\(f.dur)").font(.system(size: 9)).foregroundColor(.secondary)
                        }.onTapGesture { m.cur = i }
                    }
                }.padding(2)
            }.frame(height: 76)
        }
    }

    func boardRow(_ label: String, mode: String, addable: Bool = true, onDelete: @escaping () -> Void) -> some View {
        let now = m.nowPlaying == mode || m.bMode == mode
        return HStack(spacing: 6) {
            Image(systemName: now ? "play.circle.fill" : "circle").foregroundColor(now ? .green : .secondary)
            Text(label).fontWeight(now ? .bold : .regular).lineLimit(1)
            Spacer()
            Button { m.play(mode) } label: { Image(systemName: "play.fill") }.help("Play on the board").disabled(!m.connected)
            if addable {
                Button { m.addBoardItem(mode) } label: { Image(systemName: "text.badge.plus") }
                    .help("Add to playlist “\(m.pl.name)”")
            }
            Button { onDelete() } label: { Image(systemName: "trash") }.help("Delete from the board").disabled(!m.connected)
        }
        .buttonStyle(.borderless)
        .padding(.vertical, 1)
        .contextMenu {
            Button("Play on the board") { m.play(mode) }.disabled(!m.connected)
            if addable { Button("Add to playlist “\(m.pl.name)”") { m.addBoardItem(mode) } }
            Divider()
            Button("Delete from the board", role: .destructive) { onDelete() }.disabled(!m.connected)
        }
        .listRowBackground(now ? Color.green.opacity(0.15) : Color.clear)
    }

    var controls: some View {
        Form {
            Section("Unicorn") {
                HStack {
                    Text(m.connected ? "MQTT " + m.conn : m.conn).font(.caption).foregroundColor(m.connected ? .green : .orange)
                    Spacer(); Button("Settings…") { m.showSettings = true }
                }
                Text(m.bInfo).font(.caption2).foregroundColor(.secondary)
                Toggle("Power", isOn: Binding(get: { m.bOn }, set: { m.bOn = $0; m.light(["state": $0 ? "ON" : "OFF"]) }))
                Slider(value: $m.bBright, in: 1...100) { Text("Bright") } onEditingChanged: { e in
                    if !e { m.light(["state": "ON", "brightness": Int(m.bBright * 2.55)]) } }
                Picker("Mode", selection: Binding(get: { m.bMode }, set: { m.play($0) })) {
                    ForEach(m.modes, id: \.self) { Text($0).tag($0) }
                }
                Picker("Rotation", selection: Binding(get: { m.bRot }, set: { m.bRot = $0; m.cmd("rotation", $0) })) {
                    ForEach(m.bRots, id: \.self) { Text($0 + "°").tag($0) }
                }
                Slider(value: $m.bSpeed, in: 1...20, step: 1) { Text("Speed") } onEditingChanged: { e in
                    if !e { m.cmd("speed", String(Int(m.bSpeed))) } }
                TextField("Scroll text", text: $m.bText).onSubmit { m.cmd("text", m.bText) }
                Button("Scroll this text") { m.cmd("text", m.bText) }
                Button("Use paint color as board color") {
                    m.light(["color": ["r": Int(m.color >> 16 & 255), "g": Int(m.color >> 8 & 255), "b": Int(m.color & 255)]]) }
                Button("Back to clock") { m.play("clock") }
            }
            Section {
                if m.bCustoms.isEmpty { Text(m.connected ? "nothing uploaded yet" : "offline").font(.caption).foregroundColor(.secondary) }
                ForEach(m.bCustoms, id: \.self) { c in boardRow(c, mode: "custom:" + c) { confirmDelete = c } }
                if !m.bPlaylists.isEmpty {
                    Text("Playlists").font(.caption).foregroundColor(.secondary)
                    ForEach(m.bPlaylists, id: \.self) { p in boardRow("▶︎ " + p, mode: "playlist:" + p, addable: false) { m.deletePlaylistFromBoard(p) } }
                }
            } header: {
                HStack {
                    Text("On the board (\(m.bCustoms.count))")
                    Spacer()
                    if m.refreshing { ProgressView().controlSize(.small) }
                    Button { m.refreshBoard() } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                        .buttonStyle(.borderless).disabled(!m.connected).help("Ask the board what it has and what is playing")
                }
            } footer: {
                if m.bMode.hasPrefix("playlist:"), let p = m.bPlaying { Text("Playlist now showing: \(p)").font(.caption) }
            }
        }.formStyle(.grouped)
    }

    var playlists: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Picker("", selection: Binding(get: { m.pl.name }, set: { m.selectPlaylist($0) })) {
                    ForEach(m.plNames.contains(m.pl.name) ? m.plNames : m.plNames + [m.pl.name], id: \.self) { Text($0).tag($0) }
                }.frame(maxWidth: .infinity)
                Button { m.newPlaylist() } label: { Image(systemName: "plus") }.help("New playlist")
                Button { m.deletePlaylist() } label: { Image(systemName: "trash") }.help("Delete this playlist (here and on the board)")
            }
            HStack {
                TextField("rename", text: $renaming).textFieldStyle(.roundedBorder).onSubmit { m.renamePlaylist(renaming) }
                Button("Rename") { m.renamePlaylist(renaming) }.disabled(renaming.isEmpty)
            }.onAppear { renaming = m.pl.name }.onChange(of: m.pl.name) { renaming = $0 }
            List(selection: $plSel) {
                ForEach($m.pl.items) { $it in
                    let showing = m.bMode == "playlist:" + m.pl.name && m.bPlaying == (it.mode == "date" ? "today" : it.mode)
                    HStack(spacing: 6) {
                        Image(systemName: "line.3.horizontal").foregroundColor(.secondary)
                        Text(it.mode.hasPrefix("custom:") ? String(it.mode.dropFirst(7)) : "⌚︎ " + it.mode)
                            .fontWeight(showing ? .bold : .regular).lineLimit(1)
                        if it.mode.hasPrefix("custom:") && !m.bCustoms.contains(String(it.mode.dropFirst(7))) {
                            Image(systemName: "icloud.and.arrow.up").foregroundColor(.orange).help("Not on the board yet; uploaded on Send")
                        }
                        Spacer()
                        TextField("", value: $it.seconds, formatter: NumberFormatter()).frame(width: 44).textFieldStyle(.roundedBorder)
                            .onSubmit { it.seconds = max(2, min(3600, it.seconds)); m.savePlaylist() }
                        Stepper("", value: Binding(get: { it.seconds }, set: { it.seconds = max(2, min(3600, $0)); m.savePlaylist() }),
                                in: 2...3600).labelsHidden().help("Seconds this item is shown")
                        Text("s").font(.caption)
                        Button { m.removePlaylistItems([it.id]) } label: { Image(systemName: "minus.circle") }
                            .buttonStyle(.borderless).help("Remove from the playlist")
                    }
                    .tag(it.id)
                    .contextMenu { Button("Remove from playlist", role: .destructive) { m.removePlaylistItems([it.id]) } }
                    .listRowBackground(showing ? Color.green.opacity(0.15) : Color.clear)
                }
                .onMove { from, to in m.pl.items.move(fromOffsets: from, toOffset: to); m.savePlaylist() }
                .onDelete { idx in m.removePlaylistItems(Set(idx.map { m.pl.items[$0].id })) }
            }
            .onDeleteCommand { m.removePlaylistItems(plSel); plSel = [] }
            HStack {
                Picker("", selection: $addBuiltin) { ForEach(m.builtins + (m.builtins.contains("date") ? [] : ["date"]), id: \.self) { Text($0).tag($0) } }.frame(width: 130)
                Button("Add mode") { m.addToPlaylist(mode: addBuiltin) }
            }
            Button("Add the open display") { if let u = m.fileURL { m.addLibraryItem(u) } else { m.sendLog = "save the display to the library first" } }
                .disabled(m.fileURL == nil)
            Text("Drag rows to reorder · select + Delete key (or −) to remove · ＋ on an “On the board” row or right-click a library item to add").font(.caption2).foregroundColor(.secondary)
            if m.plUnsent { Text("Changes are saved on this Mac only; press Send to update the board.").font(.caption).foregroundColor(.orange) }
            HStack {
                Button(m.sending ? "Sending…" : "Send & play on the board") { m.savePlaylist(); m.activatePlaylist(play: true) }
                    .buttonStyle(.borderedProminent).disabled(m.sending || !m.connected || m.pl.items.isEmpty)
                Button("Send only") { m.savePlaylist(); m.activatePlaylist(play: false) }
                    .disabled(m.sending || !m.connected || m.pl.items.isEmpty).help("Store on the board without switching to it")
            }
            if m.bMode == "playlist:" + m.pl.name { Text("Playing now: \(m.bPlaying ?? "—")").font(.caption).foregroundColor(.green) }
        }.padding(8)
        .onDisappear { m.savePlaylist() }
    }
}

@main
struct UnicornStudioApp: App {
    @StateObject var m = AppModel()
    var body: some Scene {
        WindowGroup("Unicorn Studio") { ContentView(m: m).frame(minWidth: 1250, minHeight: 760) }
        .commands { CommandGroup(after: .appSettings) { Button("Connection Settings…") { m.showSettings = true }.keyboardShortcut(",") } }
    }
}
