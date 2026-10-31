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
    @Published var pl = Playlist(name: "", items: [])
    @Published var plDirty = false                // edits not saved on this Mac yet
    @Published var plSavedName: String? = nil     // the Mac file the open playlist came from / was saved as
    @Published var plSource = "new"               // picker tag: "new", "mac:<name>", "board:<name>"
    @Published var plLog = ""
    @Published var plOK: Bool? = nil
    @Published var bOnline = false
    var plAnswers: [Data] = []                    // playlist/result2 answers
    var plItems: [String: Data] = [:]             // playlist/items answers by name
    @Published var libraryLoading = false
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
                if self.pl.items.isEmpty, self.plSavedName == nil, let p = first { self.pl = p; self.plSavedName = p.name; self.plSource = "mac:" + p.name }
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
                else if t == b + "/status" {
                    let s = String(decoding: p.prefix(20), as: UTF8.self); self.bOnline = s == "online"; self.bInfo = "board " + s
                }
                else if t == b + "/playlist/result2" { self.plAnswers.append(p) }
                else if t == b + "/playlist/items" {
                    if let j = try? JSONSerialization.jsonObject(with: p) as? [String: Any], let n = j["name"] as? String { self.plItems[n] = p }
                }
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
            subs += [base + "/state", base + "/status", base + "/custom/ack", base + "/custom/result", base + "/custom/result2", base + "/playlist/result2", base + "/playlist/items"]
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
        bStateAt = Date(); refreshing = false; bOnline = true
        if autoSize, bW > 0, bH > 0, (bW != W || bH != H) { setCanvas(bW, bH) }
    }
    /// What is playing right now ("custom:x", "clock", ...), also inside a playlist.
    var nowPlaying: String { bPlaying ?? bMode }
    var bSpeedInt: Int { max(1, min(20, Int(bSpeed))) }
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
    // Flow: add items that are already ON THE BOARD (or built-in modes) -> name it -> Save (on this Mac)
    //       -> Send to board (only the playlist JSON; displays are never uploaded from here) -> Play.
    func refreshPlaylists() { plNames = PlaylistIO.list() }
    var plOnBoard: Bool { bPlaylists.contains(pl.name) }
    var plMissing: [String] { PlaylistIO.missing(pl, onBoard: bCustoms) }
    func sentKey(_ n: String) -> String { "plSent|\(base)|\(n)" }
    var plUpToDate: Bool { plOnBoard && UserDefaults.standard.string(forKey: sentKey(pl.name)) == PlaylistIO.signature(pl) }
    /// One line saying where the open playlist lives.
    var plStatus: (String, Color) {
        if plSavedName == nil {
            return (pl.items.isEmpty ? "New playlist: add items, type a name, press Save." : "Not saved yet: type a name and press Save.", .orange)
        }
        if plDirty { return ("Unsaved changes: press Save.", .orange) }
        if !plOnBoard { return ("Saved on this Mac only. Next: Send to board.", .orange) }
        if !plUpToDate { return ("On the board, but this Mac's copy is different (or was never sent from here). Send to board to update it.", .orange) }
        return ("✓ Saved on this Mac and on the board.", .green)
    }
    func edited() { plDirty = true }
    func newPlaylist() {
        pl = Playlist(name: "", items: []); plSavedName = nil; plSource = "new"; plDirty = false; plOK = nil
        plLog = "New playlist. Add items from “On the board” or the built-in modes, type a name, then Save."
    }
    func choosePlaylist(_ tag: String) {
        if tag == "new" { newPlaylist() }
        else if tag.hasPrefix("mac:") { openLocalPlaylist(String(tag.dropFirst(4))) }
        else if tag.hasPrefix("board:") { importBoardPlaylist(String(tag.dropFirst(6))) }
    }
    func openLocalPlaylist(_ n: String) {
        guard let p = PlaylistIO.load(n) else { plLog = "could not read \(n).json"; plOK = false; refreshPlaylists(); return }
        pl = p; plSavedName = n; plSource = "mac:" + n; plDirty = false; plLog = ""; plOK = nil
    }
    /// A playlist that is on the board but not on this Mac: ask the board for its items (firmware us-2.1+).
    func importBoardPlaylist(_ n: String) {
        pl = Playlist(name: n, items: []); plSavedName = nil; plSource = "board:" + n; plDirty = false; plOK = nil
        guard connected else { plLog = "offline: can't read “\(n)” from the board"; plOK = false; return }
        plLog = "asking the board for the items of “\(n)”…"
        plItems[n] = nil
        mq.publish(base + "/playlist/get", n)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { self.waitImport(n, tries: 25) }
    }
    func waitImport(_ n: String, tries: Int) {
        guard plSource == "board:" + n, pl.items.isEmpty else { return }      // the user moved on
        if let d = plItems[n] {
            if let p = PlaylistIO.fromBoard(d, name: n) {
                pl = p; plDirty = true
                UserDefaults.standard.set(PlaylistIO.signature(p), forKey: sentKey(n))
                plLog = "Imported “\(n)” (\(p.items.count) items) from the board. Press Save to keep a copy on this Mac."; plOK = true
            } else { plLog = "the board has no playlist “\(n)” any more"; plOK = false; refreshBoard() }
            return
        }
        if tries <= 0 {
            plLog = "The board did not send the items of “\(n)” (its firmware is older than us-2.1). You can still Play it; to edit it, add the items here, Save, and Send to board to replace it."
            plOK = false; return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { self.waitImport(n, tries: tries - 1) }
    }
    /// The name Save will use, or nil if the field is empty.
    var plCleanName: String? {
        let t = pl.name.trimmingCharacters(in: .whitespaces)
        return t.isEmpty ? nil : DisplayIO.boardName(t)
    }
    /// Save on this Mac under the name in the Name field (a different name than the open file = a new copy).
    @discardableResult func savePlaylist() -> Bool {
        guard let n = plCleanName else { plLog = "Type a name for the playlist first."; plOK = false; return false }
        guard !pl.items.isEmpty else { plLog = "Add at least one item before saving."; plOK = false; return false }
        guard pl.items.count <= 32 else { plLog = "A playlist can have at most 32 items (it has \(pl.items.count))."; plOK = false; return false }
        if n != plSavedName && plNames.contains(n) {
            plLog = "There is already a playlist called “\(n)” on this Mac. Pick another name, or open that one."; plOK = false; return false
        }
        pl.name = n
        do { try PlaylistIO.save(pl) } catch { plLog = "Save failed: \(error.localizedDescription)"; plOK = false; return false }
        plSavedName = n; plSource = "mac:" + n; plDirty = false; refreshPlaylists()
        plLog = "Saved “\(n)” on this Mac (\(pl.items.count) items)." + (plUpToDate ? "" : " Next: Send to board."); plOK = true
        return true
    }
    /// Rename the open (saved) playlist on this Mac to the name in the Name field.
    func renamePlaylist() {
        guard let old = plSavedName, let n = plCleanName, n != old else { return }
        guard !plNames.contains(n) else { plLog = "There is already a playlist called “\(n)” on this Mac."; plOK = false; return }
        pl.name = n
        do { try PlaylistIO.save(pl) } catch { plLog = "Rename failed: \(error.localizedDescription)"; plOK = false; return }
        PlaylistIO.delete(old); plSavedName = n; plSource = "mac:" + n; plDirty = false; refreshPlaylists()
        plLog = "Renamed to “\(n)” on this Mac." + (bPlaylists.contains(old) ? " The board still has it as “\(old)”: Send to board stores “\(n)”, and the trash button on “\(old)” in the Board tab removes the old copy." : "")
        plOK = true
    }
    /// Delete the open playlist from this Mac (the board copy is separate: Remove from board).
    func deletePlaylist() {
        if let old = plSavedName { PlaylistIO.delete(old); refreshPlaylists(); plLog = "Deleted “\(old)” from this Mac." + (bPlaylists.contains(old) ? " It is still on the board." : "") }
        pl = Playlist(name: "", items: []); plSavedName = nil; plSource = "new"; plDirty = false; plOK = nil
    }
    func removePlaylistFromBoard() {
        let n = pl.name
        guard bPlaylists.contains(n) else { return }
        deletePlaylistFromBoard(n); UserDefaults.standard.removeObject(forKey: sentKey(n))
        plLog = "Asked the board to delete “\(n)” (still saved on this Mac)."; plOK = nil
    }
    /// Add a built-in mode or a display that is already on the board. Displays only on this Mac can't be added.
    func addItem(_ mode: String, seconds: Int = 10) {
        if mode.hasPrefix("custom:"), !bCustoms.contains(String(mode.dropFirst(7))) {
            plLog = "“\(mode.dropFirst(7))” is not on the board. Upload it first (open it in the Library, then Send to Unicorn)."; plOK = false; return
        }
        guard pl.items.count < 32 else { plLog = "A playlist can have at most 32 items."; plOK = false; return }
        pl.items.append(PlaylistItem(mode: mode, seconds: max(2, min(3600, seconds)))); edited()
        let n = mode.hasPrefix("custom:") ? String(mode.dropFirst(7)) : mode
        plLog = "Added \(n). Press Save when you're done."; plOK = nil
    }
    /// The ＋ on an “On the board” row.
    func addBoardItem(_ mode: String) { addItem(mode); sendLog = plLog; sendOK = nil }
    func removePlaylistItems(_ ids: Set<UUID>) {
        guard !ids.isEmpty else { return }
        pl.items.removeAll { ids.contains($0.id) }; edited()
    }
    /// Poll our own copy of the board state (asking it to republish) until `ok` holds. Call off the main thread.
    func confirmState(timeout: Double, _ ok: @escaping (AppModel) -> Bool) -> Bool {
        let t0 = DispatchQueue.main.sync { Date() }
        var last = Date.distantPast
        while Date().timeIntervalSince(t0) < timeout {
            if Date().timeIntervalSince(last) > 3 { last = Date(); DispatchQueue.main.sync { self.cmd("refresh", "1") } }
            Thread.sleep(forTimeInterval: 0.3)
            if DispatchQueue.main.sync(execute: { self.bStateAt > t0 && ok(self) }) { return true }
        }
        return false
    }
    /// Send ONLY the playlist JSON to the board (never uploads displays), retrying through a board reboot,
    /// and report success only when the board's own state lists the playlist.
    func sendPlaylist(play doPlay: Bool) {
        guard !sending else { plLog = "busy: wait for the current send to finish"; return }
        guard connected, !base.isEmpty else { plLog = "Not connected to MQTT."; plOK = false; return }
        guard plSavedName != nil, !plDirty else { plLog = "Save the playlist first."; plOK = false; return }
        guard !pl.items.isEmpty else { plLog = "The playlist is empty."; plOK = false; return }
        let p = pl, missing = plMissing
        let id = String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(8)).lowercased()
        sending = true; plOK = nil
        func say(_ s: String) { DispatchQueue.main.async { self.plLog = s } }
        func done(_ ok: Bool, _ s: String) { DispatchQueue.main.async { self.sending = false; self.plOK = ok; self.plLog = s; self.sendLog = s; self.sendOK = ok } }
        DispatchQueue.global().async {
            var answer: (ok: Bool, msg: String)? = nil
            attempts: for attempt in 1...4 {
                var waited = 0.0      // the board may be rebooting: wait until MQTT and the board are back
                while !DispatchQueue.main.sync(execute: { self.connected && self.bOnline }) {
                    if waited == 0 { say("waiting for the board to come online…") }
                    if waited > 90 { break attempts }
                    Thread.sleep(forTimeInterval: 0.5); waited += 0.5
                }
                say(attempt == 1 ? "sending playlist “\(p.name)” to the board…" : "no answer yet; sending again (try \(attempt) of 4)…")
                DispatchQueue.main.sync { self.plAnswers = []; self.mq.publish(self.base + "/playlist/save", PlaylistIO.boardJSON(p, id: id)) }
                for _ in 0..<80 {
                    Thread.sleep(forTimeInterval: 0.1)
                    let got = DispatchQueue.main.sync { self.plAnswers }
                    if let a = got.lazy.compactMap({ PlaylistIO.saveAnswer($0, id: id, name: p.name) }).first { answer = a; break }
                }
                if answer != nil { break }
            }
            guard let a = answer else { done(false, "The board did not answer (offline or rebooting?). Not confirmed: press Send to board again."); return }
            guard a.ok else { done(false, "The board refused the playlist: \(a.msg)"); return }
            say("the board answered “\(a.msg)”; checking its playlist list…")
            guard self.confirmState(timeout: 15, { $0.bPlaylists.contains(p.name) }) else {
                done(false, "The board answered, but “\(p.name)” is not in its playlist list. Not confirmed: press Send to board again."); return
            }
            DispatchQueue.main.sync { UserDefaults.standard.set(PlaylistIO.signature(p), forKey: self.sentKey(p.name)) }
            let skip = missing.isEmpty ? "" : " Not on the board, will be skipped: " + missing.joined(separator: ", ") + "."
            if !doPlay { done(true, "✓ “\(p.name)” is on the board (\(p.items.count) items). Press Play to start it." + skip); return }
            DispatchQueue.main.sync { self.cmd("mode", "playlist:" + p.name) }
            let playing = self.confirmState(timeout: 12) { $0.bMode == "playlist:" + p.name }
            done(playing, playing ? "✓ “\(p.name)” is on the board and playing." + skip
                                  : "“\(p.name)” is on the board, but it did not confirm it started. Press Play." + skip)
        }
    }
    /// Start the open playlist on the board and confirm from the board's state.
    func playPlaylist() {
        let n = pl.name
        guard connected, bPlaylists.contains(n) else { plLog = "“\(n)” is not on the board yet: Send to board first."; plOK = false; return }
        plLog = "starting “\(n)”…"; plOK = nil
        cmd("mode", "playlist:" + n)
        DispatchQueue.global().async {
            let ok = self.confirmState(timeout: 12) { $0.bMode == "playlist:" + n }
            DispatchQueue.main.async {
                self.plOK = ok
                self.plLog = ok ? "✓ Playing “\(n)” on the board." : "The board did not confirm it is playing “\(n)” (offline?)."
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
    @State var pendingPlaylist: String? = nil
    @State var confirmPlDelete = false
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
                        Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([u]) }
                        Divider()
                        Button("Move to Trash") { m.trash(u) }
                    }
            }
            HStack {
                Button("Refresh") { m.refreshLibrary() }
                Button("Folder") { NSWorkspace.shared.open(DisplayIO.libDir) }
            }
            Text("Click to open · double-click to send to the board. Playlists use what is on the board.").font(.caption2).foregroundColor(.secondary)
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
                    .help("Add to the open playlist (Playlists tab)")
            }
            Button { onDelete() } label: { Image(systemName: "trash") }.help("Delete from the board").disabled(!m.connected)
        }
        .buttonStyle(.borderless)
        .padding(.vertical, 1)
        .contextMenu {
            Button("Play on the board") { m.play(mode) }.disabled(!m.connected)
            if addable { Button("Add to the open playlist") { m.addBoardItem(mode) } }
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
        let missing = m.plMissing
        let (status, statusColor) = m.plStatus
        let boardOnly = m.bPlaylists.filter { !m.plNames.contains($0) }
        let renamable = m.plSavedName != nil && m.plCleanName != nil && m.plCleanName != m.plSavedName
        return VStack(alignment: .leading, spacing: 7) {
            Text("① Add items from “On the board” or a built-in mode  ② Name it  ③ Save  ④ Send to board  ⑤ Play")
                .font(.caption2).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Picker("Open", selection: Binding(get: { m.plSource }, set: { t in
                    if m.plDirty && t != m.plSource { pendingPlaylist = t } else { m.choosePlaylist(t) } })) {
                    Text("— new playlist —").tag("new")
                    if !m.plSource.hasPrefix("mac:") && m.plSource != "new" && !boardOnly.contains(String(m.plSource.dropFirst(6))) {
                        Text(String(m.plSource.dropFirst(6))).tag(m.plSource)
                    }
                    Section("Saved on this Mac") {
                        ForEach(m.plNames, id: \.self) { n in Text(n + (m.bPlaylists.contains(n) ? "   (also on the board)" : "   (Mac only)")).tag("mac:" + n) }
                    }
                    Section("Only on the board") {
                        ForEach(boardOnly, id: \.self) { n in Text(n + "   (board only: import)").tag("board:" + n) }
                    }
                }
                Button { if m.plDirty { pendingPlaylist = "new" } else { m.newPlaylist() } } label: { Image(systemName: "plus") }.help("New playlist")
                Button { confirmPlDelete = true } label: { Image(systemName: "trash") }.disabled(m.plSavedName == nil)
                    .help("Delete this playlist from this Mac (the board copy stays; use Remove from board)")
            }
            HStack {
                Text("Name")
                TextField("e.g. halloween", text: Binding(get: { m.pl.name }, set: { m.pl.name = $0; m.edited() }))
                    .textFieldStyle(.roundedBorder).onSubmit { m.savePlaylist() }
                if renamable { Button("Rename") { m.renamePlaylist() }.help("Rename the saved playlist on this Mac") }
                Button(renamable ? "Save copy" : "Save") { m.savePlaylist() }
                    .buttonStyle(.borderedProminent).tint(m.plDirty || m.plSavedName == nil ? .accentColor : .gray)
                    .disabled(m.pl.items.isEmpty || m.plCleanName == nil)
                    .help("Save this playlist on this Mac")
            }
            Text(status).font(.caption).foregroundColor(statusColor).fixedSize(horizontal: false, vertical: true)
            List(selection: $plSel) {
                if m.pl.items.isEmpty {
                    Text("No items yet. Add displays with ＋ on the Board tab (“On the board”), or a built-in mode below.").font(.caption).foregroundColor(.secondary)
                }
                ForEach($m.pl.items) { $it in
                    let showing = m.bMode == "playlist:" + m.pl.name && m.bPlaying == (it.mode == "date" ? "today" : it.mode)
                    let absent = it.mode.hasPrefix("custom:") && !m.bCustoms.contains(String(it.mode.dropFirst(7)))
                    VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Image(systemName: "line.3.horizontal").foregroundColor(.secondary)
                        Text(it.mode.hasPrefix("custom:") ? String(it.mode.dropFirst(7)) : "⌚︎ " + it.mode)
                            .fontWeight(showing ? .bold : .regular).foregroundColor(absent ? .orange : .primary).lineLimit(1)
                        if absent {
                            Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.orange)
                                .help("Not on the board: the board will skip it. Upload it first (open it in the Library → Send to Unicorn).")
                        }
                        Spacer()
                        Button { m.removePlaylistItems([it.id]) } label: { Image(systemName: "minus.circle") }
                            .buttonStyle(.borderless).help("Remove from the playlist")
                    }
                    HStack(spacing: 4) {
                        TextField("", value: Binding(get: { it.seconds }, set: { it.seconds = max(2, min(3600, $0)); m.edited() }), formatter: NumberFormatter())
                            .frame(width: 44).textFieldStyle(.roundedBorder)
                        Stepper("", value: Binding(get: { it.seconds }, set: { it.seconds = max(2, min(3600, $0)); m.edited() }),
                                in: 2...3600).labelsHidden().help("Seconds this item is shown")
                        Text("s").font(.caption)
                        Spacer(minLength: 8)
                        Text("Speed").font(.caption).foregroundColor(.secondary)
                        Text(it.speed.map { "\($0)" } ?? "–").font(.caption.monospacedDigit()).frame(width: 18, alignment: .trailing)
                        Stepper("", value: Binding(get: { it.speed ?? m.bSpeedInt }, set: { it.speed = max(1, min(20, $0)); m.edited() }),
                                in: 1...20).labelsHidden()
                            .help("Animation speed while this item plays: 10 = normal (as made), lower = slower, higher = faster")
                        Text(PlaylistItem.speedLabel(it.speed)).font(.caption)
                            .foregroundColor(it.speed == PlaylistItem.normalSpeed ? .secondary : .blue).frame(width: 64, alignment: .leading)
                    }.padding(.leading, 20)
                    }
                    .tag(it.id)
                    .contextMenu { Button("Remove from playlist", role: .destructive) { m.removePlaylistItems([it.id]) } }
                    .listRowBackground(showing ? Color.green.opacity(0.15) : Color.clear)
                }
                .onMove { from, to in m.pl.items.move(fromOffsets: from, toOffset: to); m.edited() }
                .onDelete { idx in m.removePlaylistItems(Set(idx.map { m.pl.items[$0].id })) }
            }
            .onDeleteCommand { m.removePlaylistItems(plSel); plSel = [] }
            HStack {
                Picker("Add", selection: $addBuiltin) {
                    Section("Built-in") { ForEach(m.builtins + (m.builtins.contains("date") ? [] : ["date"]), id: \.self) { Text($0).tag($0) } }
                    Section("On the board") { ForEach(m.bCustoms, id: \.self) { Text($0).tag("custom:" + $0) } }
                }
                Button("Add") { m.addItem(addBuiltin) }
            }
            Text("Speed per item: 10 = normal (as the animation was made), 1 = slowest, 20 = fastest. Items without their own speed use the board's Speed slider (now \(m.bSpeedInt)).")
                .font(.caption2).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("Only displays already on the board can be added. New animation? Open it in the Library and choose Send to Unicorn first.")
                .font(.caption2).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
            if !missing.isEmpty {
                Text("⚠︎ Not on the board (will be skipped): " + missing.joined(separator: ", ")).font(.caption).foregroundColor(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button(m.sending ? "Sending…" : "Send to board") { m.sendPlaylist(play: false) }
                    .buttonStyle(.borderedProminent)
                    .disabled(m.sending || !m.connected || m.plSavedName == nil || m.plDirty || m.pl.items.isEmpty)
                    .help(m.plDirty || m.plSavedName == nil ? "Save first" : "Store this playlist on the board (sends only the playlist, never animations)")
                Button("▶︎ Play") { m.playPlaylist() }
                    .disabled(m.sending || !m.connected || !m.plOnBoard).help("Play this playlist on the board")
                Button("Send & play") { m.sendPlaylist(play: true) }
                    .disabled(m.sending || !m.connected || m.plSavedName == nil || m.plDirty || m.pl.items.isEmpty)
            }
            if m.plOnBoard {
                Button("Remove from board") { m.removePlaylistFromBoard() }.disabled(m.sending || !m.connected)
                    .help("Delete this playlist from the board (the copy on this Mac stays)")
            }
            if !m.plLog.isEmpty {
                Text(m.plLog).font(.caption).foregroundColor(m.plOK == false ? .red : (m.plOK == true ? .green : .secondary))
                    .fixedSize(horizontal: false, vertical: true)
            }
            if m.bMode == "playlist:" + m.pl.name { Text("Playing now: \(m.bPlaying ?? "—")").font(.caption).foregroundColor(.green) }
        }.padding(8)
        .alert("Discard unsaved changes to “\(m.pl.name.isEmpty ? "the new playlist" : m.pl.name)”?",
               isPresented: Binding(get: { pendingPlaylist != nil }, set: { if !$0 { pendingPlaylist = nil } })) {
            Button("Discard", role: .destructive) { if let t = pendingPlaylist { m.choosePlaylist(t) }; pendingPlaylist = nil }
            Button("Cancel", role: .cancel) { pendingPlaylist = nil }
        } message: { Text("Press Cancel, then Save, to keep them.") }
        .alert("Delete “\(m.plSavedName ?? "")” from this Mac?", isPresented: $confirmPlDelete) {
            Button("Delete", role: .destructive) { m.deletePlaylist() }
            Button("Cancel", role: .cancel) {}
        } message: { Text(m.plOnBoard ? "The copy on the board is kept (use Remove from board for that)." : "It is not on the board.") }
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
