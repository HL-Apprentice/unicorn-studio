// Headless checks for Core.swift: resampling, file round-trips at every board size, playlists, config parsing, MQTT framing.
// Build+run: xcrun swiftc -parse-as-library Sources/Core.swift selftest/main.swift -o /tmp/us-selftest && /tmp/us-selftest
import Foundation

@main struct SelfTest {
    static var fails = 0
    static func check(_ c: Bool, _ what: String) { print((c ? "PASS " : "FAIL ") + what); if !c { fails += 1 } }
    static func main() {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("us-selftest-\(getpid())")
        try? FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        setenv("UNICORN_LIBRARY", tmp.path, 1)
        // resample: 32x32 solid -> every size stays solid in fill/stretch; fit letterboxes the Galactic
        let red = Display(name: "r", desc: "", w: 32, h: 32, frames: [Frame(px: [UInt32](repeating: 0xFF0000, count: 1024), dur: 100)])
        for k in BoardKind.all {
            let f = red.resized(k.w, k.h, mode: .fill).frames[0].px
            check(f.count == k.w * k.h && f.allSatisfy { $0 == 0xFF0000 }, "fill 32x32 -> \(k.label) solid")
        }
        let fit = red.resized(53, 11, mode: .fit).frames[0].px
        let lit = fit.filter { $0 != 0 }.count
        check(lit == 11 * 11, "fit 32x32 -> 53x11 gives an 11x11 centred square (\(lit) px)")
        check(fit[5 * 53 + 26] == 0xFF0000 && fit[5 * 53 + 0] == 0, "fit is centred")
        // 2:1 downsample averages
        var cb = [UInt32](repeating: 0, count: 1024); for i in 0..<1024 where (i % 32 + i / 32) % 2 == 0 { cb[i] = 0xFFFFFF }
        let half = Resample.px(cb, 32, 32, 16, 16, .fit)
        check(half.allSatisfy { ($0 >> 16) & 255 >= 126 && ($0 >> 16) & 255 <= 129 }, "checkerboard 32->16 averages to grey")
        let up2 = Resample.px([0x123456], 1, 1, 16, 16, .stretch)
        check(up2.allSatisfy { $0 == 0x123456 }, "upscale is exact")
        // round-trip save/load at each size
        for k in BoardKind.all {
            var px = [UInt32](repeating: 0, count: k.w * k.h); for i in px.indices { px[i] = UInt32(i * 2654435761 & 0xFFFFFF) }
            let d = Display(name: "t_\(k.id)", desc: "x", w: k.w, h: k.h, frames: [Frame(px: px, dur: 123), Frame(w: k.w, h: k.h)])
            let u = tmp.appendingPathComponent("rt_\(k.id).json")
            do { try DisplayIO.save(d, to: u); let e = try DisplayIO.load(u)
                check(e.w == k.w && e.h == k.h && e.frames[0].px == px && e.frames[0].dur == 123 && e.frames.count == 2, "save/load round trip \(k.label)")
            } catch { check(false, "round trip \(k.label): \(error)") }
        }
        // legacy 32x32 file without width/height
        let legacy = tmp.appendingPathComponent("legacy.json")
        try? #"{"frames":[{"duration_ms":50,"pixels":"\#(String(repeating: "00ff00", count: 1024))"}]}"#.write(to: legacy, atomically: true, encoding: .utf8)
        if let l = try? DisplayIO.load(legacy) { check(l.w == 32 && l.h == 32 && l.frames[0].px[0] == 0x00FF00, "legacy file loads as 32x32") } else { check(false, "legacy load") }
        // hostile files
        let bad = tmp.appendingPathComponent("bad.json")
        try? #"{"width":100000,"height":100000,"frames":[{}]}"#.write(to: bad, atomically: true, encoding: .utf8)
        check((try? DisplayIO.load(bad)) == nil, "oversize width/height rejected")
        try? "not json".write(to: bad, atomically: true, encoding: .utf8)
        check((try? DisplayIO.load(bad)) == nil, "garbage rejected")
        check(DisplayIO.boardName("../../Evil Name!!") == "______evil_name__", "board names are sanitised: \(DisplayIO.boardName("../../Evil Name!!"))")
        // playlists
        let p = Playlist(name: "test", items: [PlaylistItem(mode: "custom:alien_head", seconds: 1, file: "UFO/json/alien_head.json"), PlaylistItem(mode: "clock", seconds: 99999)])
        try? PlaylistIO.save(p)
        let lp = PlaylistIO.load("test")
        check(lp?.name == "test" && lp?.items.map { "\($0.mode)|\($0.seconds)|\($0.file ?? "")" } == p.items.map { "\($0.mode)|\($0.seconds)|\($0.file ?? "")" }, "playlist save/load")
        check(!DisplayIO.list().contains { DisplayIO.rel($0).hasPrefix("Playlists/") }, "playlists are not listed as displays")
        let bj = try? JSONSerialization.jsonObject(with: PlaylistIO.boardJSON(p)) as? [String: Any]
        let its = bj?["items"] as? [[String: Any]] ?? []
        check(its.count == 2 && its[0]["seconds"] as? Int == 2 && its[1]["seconds"] as? Int == 3600 && its[0]["file"] == nil, "board playlist JSON clamps seconds, has no local paths")
        let bj2 = try? JSONSerialization.jsonObject(with: PlaylistIO.boardJSON(p, id: "x1")) as? [String: Any]
        check(bj2?["id"] as? String == "x1" && bj2?["name"] as? String == "test", "board playlist JSON carries the save id")
        check(PlaylistIO.signature(p) == PlaylistIO.signature(Playlist(name: "test", items: p.items.map { PlaylistItem(mode: $0.mode, seconds: $0.seconds) })),
              "playlist signature ignores local file paths")
        check(PlaylistIO.missing(Playlist(name: "m", items: [PlaylistItem(mode: "custom:a"), PlaylistItem(mode: "custom:b"), PlaylistItem(mode: "custom:a"), PlaylistItem(mode: "clock")]), onBoard: ["b"]) == ["a"],
              "playlist items not on the board are flagged (once each), built-ins never")
        let ans = { (s: String) in Data(s.utf8) }
        check(PlaylistIO.saveAnswer(ans(#"{"id":"x1","ok":true,"msg":"ok playlist test 2 items"}"#), id: "x1", name: "test")?.ok == true, "save answer matched by id")
        check(PlaylistIO.saveAnswer(ans(#"{"id":"x1","ok":false,"msg":"error playlist/save: bad"}"#), id: "x1", name: "test")?.ok == false, "save refusal reported")
        check(PlaylistIO.saveAnswer(ans(#"{"id":"zz","ok":true,"msg":"ok playlist test 2 items"}"#), id: "x1", name: "test") == nil, "someone else's answer ignored")
        check(PlaylistIO.saveAnswer(ans(#"{"id":"","ok":true,"msg":"ok playlist test 2 items"}"#), id: "x1", name: "test")?.ok == true
              && PlaylistIO.saveAnswer(ans(#"{"id":"","ok":true,"msg":"ok playlist test2 2 items"}"#), id: "x1", name: "test") == nil, "us-2.0 answer (no id) matched by exact name")
        let fb = PlaylistIO.fromBoard(ans(#"{"name":"bp","items":[{"mode":"clock","seconds":5},{"mode":"custom:x","seconds":12,"speed":15}]}"#), name: "bp")
        check(fb?.items.map { "\($0.mode)|\($0.seconds)|\($0.speed.map(String.init) ?? "-")" } == ["clock|5|-", "custom:x|12|15"] && PlaylistIO.fromBoard(ans(#"{"name":"bp","error":"no such playlist"}"#), name: "bp") == nil,
              "board playlist import (speed kept; missing = board speed)")
        // per-item speed: default Normal (10), clamped, sent to the board, older files load as Normal
        let sp = Playlist(name: "sp", items: [PlaylistItem(mode: "custom:a"), PlaylistItem(mode: "clock", speed: 99), PlaylistItem(mode: "matrix", speed: nil)])
        let spj = (try? JSONSerialization.jsonObject(with: PlaylistIO.boardJSON(sp)) as? [String: Any])?["items"] as? [[String: Any]] ?? []
        check(spj.map { $0["speed"] as? Int ?? -1 } == [10, 20, -1], "board JSON: speed default 10, clamped, omitted when unset")
        try? FileManager.default.createDirectory(at: PlaylistIO.dir, withIntermediateDirectories: true)
        try? Data(#"{"name":"old","items":[{"mode":"clock","seconds":5}]}"#.utf8).write(to: PlaylistIO.dir.appendingPathComponent("old.json"))
        check(PlaylistIO.load("old")?.items.first?.speed == 10, "older playlist files load with Normal speed")
        check(PlaylistIO.signature(sp) != PlaylistIO.signature(Playlist(name: "sp", items: sp.items.map { var i = $0; i.speed = 10; return i })), "speed change counts as a change to send")
        // config file parsing (with a pointer file)
        let c1 = tmp.appendingPathComponent("config.env"), c2 = tmp.appendingPathComponent("other.env")
        try? "MQTT_BROKER=broker.example\nSECRETS_FILE=\(c2.path)\n".write(to: c1, atomically: true, encoding: .utf8)
        try? "export MQTT_USER='u'\nMQTT_PASSWORD=\"p w\"\nMQTT_BROKER=ignored\n".write(to: c2, atomically: true, encoding: .utf8)
        setenv("UNICORN_CONFIG", c1.path, 1)
        let fv = Config.fileValues()
        check(fv["MQTT_BROKER"] == "broker.example" && fv["MQTT_USER"] == "u" && fv["MQTT_PASSWORD"] == "p w", "config file + SECRETS_FILE pointer")
        // MQTT framing: malformed length is rejected, oversize rejected, normal publish parsed
        let mq = MQTT(); var got = [String]()
        mq.onMessage = { t, d in got.append(t + "=" + String(decoding: d, as: UTF8.self)) }
        let v = MQTT.str("a/b") + Data("hi".utf8)
        mq.buf = Data([0x30]) + MQTT.vlen(v.count) + v
        check(mq.parse() && got == ["a/b=hi"], "publish parsed")
        mq.buf = Data([0x30, 0xFF, 0xFF, 0xFF, 0xFF, 0x7F]); check(!mq.parse(), "5-byte remaining length rejected")
        mq.buf = Data([0x30]) + MQTT.vlen(MQTT.maxPacket + 1); check(!mq.parse(), "oversize packet rejected")
        mq.buf = Data([0x30, 0x05, 0x00]); check(mq.parse(), "partial packet waits")
        try? FileManager.default.removeItem(at: tmp)
        print(fails == 0 ? "ALL PASS" : "\(fails) FAILED"); exit(fails == 0 ? 0 : 1)
    }
}
