#!/usr/bin/env python3
"""verify_build_hub.py — GATE KONSOLIDASI PROYEK & KONSTRUKSI (Fase 46), acuan `docs/v2/29`.

Janji yang dijaga gate ini (semuanya cacat NYATA yang ditemukan/ditutup di fase ini):

  1. **Hub `/build` = 6 tab, satu lapis, tanpa pintu sidebar baru.** Sebelumnya tab
     "Progres & Mutu" membawa 7 sub-tab (tab di dalam tab) dan Papan Unit hanya berisi
     kolom penjualan. Gate memeriksa 6 kunci tab + ledger pintu resmi tidak bertambah.
  2. **Tidak ada fitur konstruksi lama yang hilang** (dok 29 §6 DoD-1): setiap panel lama
     (papan mandor, antrean kerja, infrastruktur kawasan, monitoring jadwal, QC, rapor
     mingguan, analitik telat, kalibrasi, buku harian, punch, kalender, template) harus
     tetap dirender di salah satu tab hub — dibuktikan per berkas, bukan per janji.
  3. **POC core hijau**: `poc/poc_46.py` dijalankan sebagai bagian gate.
  4. **Papan Unit berbaris per UNIT** dan menampilkan unit yang BELUM dijadwalkan (papan
     lama per-jadwal menyembunyikannya) + kolom deviasi/umur telat/PIC/bukti terakhir.
  5. **0 ≠ belum ada data**: unit tanpa jadwal → `planned_progress`/`deviation`/`days_late`
     = null + `missing[]`; DP tanpa rencana bayar → null, bukan "belum bayar"; rata-rata
     progres hanya dari unit terjadwal.
  6. **Satu kebenaran progres**: angka papan == Σ bobot item terverifikasi (rumus
     `build_engine`), bukan hitungan kedua.
  7. **Satu rumus kesiapan**: `readiness` di tabel == `build_readiness.evaluate()`.
  8. **Gerbang "Mulai Bangun" bergigi tapi jujur**: bawaan `build.require_dp_before_start`
     MATI → peringatan yang WAJIB diakui + beralasan (≥5 huruf); dinyalakan → benar-benar
     MENOLAK (uji negatif ON/OFF). Alasan penolakan selalu bisa dibaca manusia.
  9. **Pemisahan tugas**: memulai pembangunan butuh `construction:approve` (pelaksana
     lapangan 403), dan sales 403 di seluruh papan/izin.
 10. **Izin menempel objek**: rantai unit→blok→cluster→proyek ter-resolve; izin `approved`
     yang tanggalnya lewat dilaporkan `expired` (dulu tampak aman); izin tanpa tanggal
     berlaku ditulis "belum dicatat", bukan "aman"; izin wajib yang hilang memblokir HANYA
     bila kodenya didaftarkan admin.
 11. **Peringatan masa berlaku bergigi**: `POST /permits/alerts/scan` menghasilkan
     notifikasi + tugas Work Hub yang menyebut izinnya.
 12. **Layar tidak menuliskan kosakata sendiri**: `permit_scope`, `permit_health`,
     `build_readiness_state`, `build_gate_code` dibaca dari SSOT `/api/reference`.

Exit !=0 bila ada FAIL. Uji-mutasi: `scripts/mutasi_46.py`.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "backend" / ".env")
sys.path.insert(0, str(ROOT / "backend"))
BASE = "http://localhost:8001/api"
PW = "Sipro#2026"
FE = ROOT / "frontend" / "src"
LEDGER_DOC = ROOT / "docs" / "v2" / "40_PETA_NAV_V2.md"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
fails = []

HUB_TABS = ("unit", "kalender", "lapangan", "mutu", "analitik", "template")
# panel lama -> berkas tab hub yang WAJIB merendernya (dok 29 §6 DoD-1)
LEGACY_PANELS = {
    "ForemanBoard": "components/build/BuildFieldTab.js",
    "BuildQueuePanel": "components/build/BuildFieldTab.js",
    "SiteDiaryPanel": "components/build/BuildFieldTab.js",
    "PunchListPanel": "components/build/BuildFieldTab.js",
    "OfflineQueuePanel": "components/build/BuildFieldTab.js",
    "InspectionsPanel": "components/build/BuildQualityTab.js",
    "WeeklyReportPanel": "components/build/BuildAnalyticsTab.js",
    "DelayAnalyticsPanel": "components/build/BuildAnalyticsTab.js",
    "BuildCalibrationPage": "components/build/BuildAnalyticsTab.js",
    "BuildTemplatePanel": "components/build/BuildTemplatesTab.js",
    "ProjectPhasesPanel": "components/build/UnitBoardTab.js",
    "BuildMonitorPanel": "components/build/UnitBoardTab.js",
    "BuildCalendarPage": "pages/BuildHubPage.js",
}
SSOT_GROUPS = ("permit_scope", "permit_health", "build_readiness_state", "build_gate_code")
# label SSOT yang TIDAK boleh diketik ulang di frontend (kamus label ganda = SSOT pecah)
FORBIDDEN_LABELS = ("Menjelang kedaluwarsa", "Boleh dimulai dengan peringatan",
                    "Belum bisa dimulai", "Aktif & aman")
FE_FILES = (sorted((FE / "components" / "build").glob("*.js"))
            + sorted((FE / "components" / "permits").glob("*.js"))
            + [FE / "pages" / "BuildHubPage.js", FE / "pages" / "PermitsPage.js",
               FE / "pages" / "UnitDetailPage.js"])


def check(cond, name, detail=""):
    """Catatan: urutan argumen (cond, name) mengikuti gaya `verify_37.py`."""
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        fails.append(name)
    return bool(cond)


def head(title):
    print(f"\n{title}\n" + "-" * len(title))


def read(path) -> str:
    p = pathlib.Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def login(email: str) -> dict:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": PW}, timeout=15)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# =============================================================== A. IA & konsolidasi
def audit_ia():
    head("A. Hub `/build` 6 tab, tanpa pintu sidebar baru, tanpa fitur hilang")
    hub = read(FE / "pages" / "BuildHubPage.js")
    keys = re.findall(r'key:\s*"([a-z_]+)"', hub)
    check(tuple(keys) == HUB_TABS, "hub punya 6 tab sesuai dok 29 §1",
          f"ditemukan {keys}")
    nav = read(FE / "config" / "navigationConfig.js")
    ledger = re.search(r"<!-- NAV_DOOR_LEDGER -->\s*```json\s*(\[.*?\])\s*```",
                       read(LEDGER_DOC), re.S)
    check(bool(ledger), "ledger pintu resmi ada di docs/v2/40")
    if ledger:
        routes = {row["route"] for row in json.loads(ledger.group(1))}
        nav_paths = set(re.findall(r'path:\s*"(/[^"]*)"', nav))
        foreign = sorted(p for p in nav_paths if p not in routes and not p.startswith("/admin"))
        check(not foreign, "tidak ada pintu sidebar baru (semua terdaftar di ledger)",
              ", ".join(foreign))
    for panel, rel in LEGACY_PANELS.items():
        check(panel in read(FE / rel), f"fitur lama '{panel}' tetap ada di {rel}")
    unit_page = read(FE / "pages" / "UnitDetailPage.js")
    check("UnitBuildTab" in unit_page,
          "Unit 360 → tab Pembangunan memakai surface kerja penuh (bukan 4 baris read-only)")
    check("PermitCoveragePanel" in unit_page,
          "Unit 360 → tab Dokumen & Izin memakai panel izin bertingkat")
    check("PermitCoveragePanel" in read(FE / "pages" / "ProjectDetailPage.js"),
          "halaman proyek punya tab Dokumen & Perizinan")
    body = "\n".join(read(f) for f in FE_FILES)
    for label in FORBIDDEN_LABELS:
        check(label not in body, f"label SSOT '{label}' tidak diketik ulang di frontend")
    ref = requests.get(f"{BASE}/reference", headers=login("pm@sipro.co.id"),
                       timeout=20).json()
    groups = ref.get("data") or ref
    for g in SSOT_GROUPS:
        check(g in groups, f"grup SSOT '{g}' tersedia dari /api/reference")


# =============================================================== B. POC core
def audit_poc():
    head("B. POC core Fase 46 tetap hijau")
    r = subprocess.run([sys.executable, str(ROOT / "poc" / "poc_46.py")],
                       capture_output=True, text=True, timeout=900)
    check(r.returncode == 0, "poc/poc_46.py PASS",
          (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "")


# =============================================================== C. Papan Unit
def audit_board(pm):
    head("C. Papan Unit per-UNIT: kolom lengkap, satu kebenaran, jujur saat data kosong")
    r = requests.get(f"{BASE}/build/board/units", headers=pm, params={"limit": 200}, timeout=60)
    check(r.status_code == 200, "GET /build/board/units 200", str(r.status_code))
    if r.status_code != 200:
        return None
    body = r.json()
    rows = body["data"]
    units_total = db.units.count_documents({"org_id": "org-sipro"})
    check(len(rows) == units_total,
          "papan menampilkan SEMUA unit (termasuk yang belum dijadwalkan)",
          f"{len(rows)} baris / {units_total} unit")
    no_sched = [x for x in rows if not x["schedule_id"]]
    check(bool(no_sched), "ada unit belum dijadwalkan untuk diuji", f"{len(no_sched)} unit")
    zeros = [x["code"] for x in no_sched
             if x["planned_progress"] == 0 or x["deviation"] == 0 or x["days_late"] == 0]
    check(not zeros, "unit tanpa jadwal TIDAK dilaporkan 0 (planned/deviasi/telat)",
          ", ".join(zeros[:5]))
    check(all(x["planned_progress"] is None and x["deviation"] is None
              and x["days_late"] is None and "jadwal_pembangunan" in x["missing"]
              for x in no_sched),
          "unit tanpa jadwal → null + missing['jadwal_pembangunan']")
    unknown_dp = [x for x in rows if not x["dp_known"]]
    check(bool(unknown_dp) and all(x["dp_paid"] is None and "rencana_bayar" in x["missing"]
                                   for x in unknown_dp),
          "DP tanpa rencana bayar → null + missing['rencana_bayar']",
          f"{len(unknown_dp)} unit")
    # satu kebenaran: angka papan == Σ bobot item terverifikasi
    bad = []
    for x in [y for y in rows if y["schedule_id"]]:
        items = list(db.build_items.find({"schedule_id": x["schedule_id"]},
                                         {"_id": 0, "weight": 1, "status": 1}))
        total_w = sum(float(i.get("weight") or 0) for i in items) or 1
        done_w = sum(float(i.get("weight") or 0) for i in items if i["status"] == "done")
        if abs(round(done_w / total_w * 100, 1) - float(x["actual_progress"])) > 0.05:
            bad.append(x["code"])
    check(not bad, "progres papan == Σ bobot item terverifikasi (satu kebenaran)",
          ", ".join(bad))
    cols = ("deviation_days", "days_late", "active_step", "pic", "last_evidence",
            "readiness", "readiness_codes")
    sample = next((x for x in rows if x["schedule_id"] and x["days_late"]), None)
    check(sample is not None and all(k in sample for k in cols),
          "kolom dok 29 §4 ada (deviasi, umur telat, langkah aktif, PIC, bukti terakhir)")
    if sample:
        check(bool(sample["active_step"] and sample["pic"] and sample["last_evidence"]),
              f"unit telat {sample['code']} punya langkah aktif + PIC + bukti terakhir")
    s = body["summary"]
    prog = [x["actual_progress"] for x in rows if x["actual_progress"] is not None]
    check(s["avg_progress"] is not None
          and abs(s["avg_progress"] - round(sum(prog) / max(1, len(prog)), 1)) < 0.06,
          "rata-rata progres dihitung HANYA dari unit terjadwal", str(s["avg_progress"]))
    check(s["scheduled"] + s["unscheduled"] == s["units_total"],
          "ringkasan memisahkan terjadwal vs belum dijadwalkan")
    check(body["mode"]["require_dp_before_start"] is False
          and body["mode"]["block_build_without"] == [],
          "kebijakan bawaan MATI (peringatan, bukan blokir)", str(body["mode"]))
    # satu rumus kesiapan (tabel == evaluator)
    diff = []
    for x in rows[:6]:
        ev = requests.get(f"{BASE}/build/unit/{x['unit_id']}/readiness",
                          headers=pm, timeout=30).json()["data"]
        if ev["state"] != x["readiness"]:
            diff.append(f"{x['code']}: {x['readiness']} vs {ev['state']}")
    check(not diff, "kesiapan di tabel == build_readiness.evaluate() (bukan dua rumus)",
          "; ".join(diff))
    return rows


# =============================================================== D. Gerbang mulai bangun
def audit_gate(pm, owner, site, rows):
    head("D. Gerbang 'Mulai Bangun': peringatan wajib diakui, mode tegas benar-benar menolak")
    cand = next((x for x in rows if x["readiness"] == "warning"), None)
    blocked = next((x for x in rows if x["readiness"] == "blocked"), None)
    check(cand is not None, "ada unit berstatus peringatan untuk diuji",
          (cand or {}).get("code", "tidak ada"))
    if not cand:
        return
    uid = cand["unit_id"]
    r = requests.post(f"{BASE}/build/unit/{uid}/start", headers=pm, json={}, timeout=30)
    check(r.status_code == 400 and "peringatan" in r.text.lower(),
          "start tanpa pengakuan DITOLAK + menyebut peringatannya",
          f"{r.status_code} {r.text[:80]}")
    r = requests.post(f"{BASE}/build/unit/{uid}/start", headers=pm,
                      json={"ack": True, "reason": "ok"}, timeout=30)
    check(r.status_code in (400, 422) and "minimal" in r.text.lower(),
          "alasan terlalu pendek DITOLAK", f"{r.status_code} {r.text[:80]}")
    r = requests.post(f"{BASE}/build/unit/{uid}/start", headers=site,
                      json={"ack": True, "reason": "pelaksana mencoba memulai"}, timeout=30)
    check(r.status_code == 403,
          "pemisahan tugas: pelaksana lapangan TIDAK boleh menekan mulai bangun (403)",
          str(r.status_code))
    if blocked:
        r = requests.post(f"{BASE}/build/unit/{blocked['unit_id']}/start", headers=pm,
                          json={"ack": True, "reason": "mencoba memulai tanpa jadwal"},
                          timeout=30)
        check(r.status_code == 400 and "Belum bisa dimulai" in r.text,
              "unit tanpa jadwal ditolak dengan alasan yang bisa dibaca",
              f"{r.status_code} {r.text[:70]}")
    # uji negatif: admin menyalakan kebijakan → alasan yang sama menjadi PENGHALANG
    on = requests.put(f"{BASE}/settings/build.require_dp_before_start", headers=owner,
                      json={"value": True, "reason": "Uji gate Fase 46: mode tegas."},
                      timeout=30)
    check(on.status_code == 200, "setting kebijakan bisa dinyalakan admin", str(on.status_code))
    try:
        ev = requests.get(f"{BASE}/build/unit/{uid}/readiness", headers=pm,
                          timeout=30).json()["data"]
        check(ev["state"] == "blocked" and not ev["can_start"]
              and any(x["code"] in ("dp_unpaid", "no_payment_plan")
                      for x in ev["blockers"]),
              "mode TEGAS: DP belum terbukti → blocked (uji negatif)", ev["state"])
        r = requests.post(f"{BASE}/build/unit/{uid}/start", headers=pm,
                          json={"ack": True, "reason": "tetap mulai walau DP belum"},
                          timeout=30)
        check(r.status_code == 400 and "Belum bisa dimulai" in r.text,
              "mode TEGAS benar-benar MENOLAK start", f"{r.status_code} {r.text[:70]}")
    finally:
        requests.post(f"{BASE}/settings/build.require_dp_before_start/reset",
                      headers=owner, timeout=30)
    ev = requests.get(f"{BASE}/build/unit/{uid}/readiness", headers=pm,
                      timeout=30).json()["data"]
    check(ev["state"] == "warning" and ev["needs_ack"],
          "kebijakan dikembalikan → kembali PERINGATAN (butuh pengakuan)", ev["state"])


# =============================================================== E. Izin bertingkat
def audit_permits(pm, owner, sales, rows):
    head("E. Izin menempel objek: rantai, kedaluwarsa, izin wajib, peringatan bergigi")
    unit = next((x for x in rows if x["schedule_id"]), rows[0])
    cov = requests.get(f"{BASE}/permits/coverage", headers=pm,
                       params={"unit_id": unit["unit_id"]}, timeout=30)
    check(cov.status_code == 200, "GET /permits/coverage?unit_id 200", str(cov.status_code))
    data = cov.json()["data"]
    chain = data["chain"]
    check(all(chain.get(k) for k in ("unit_id", "block_id", "cluster_id", "project_id")),
          "rantai objek unit → blok → cluster → proyek ter-resolve",
          str(chain.get("labels")))
    scopes = {p["scope"] for p in data["permits"]}
    check("project" in scopes and len(scopes) >= 2,
          "izin warisan (proyek/cluster/blok) ikut terbaca untuk unit",
          ", ".join(sorted(scopes)))
    healths = {p["health"] for p in data["permits"]}
    check("expiring" in healths or "expired" in healths,
          "kesehatan izin dinilai dari masa berlaku (bukan hanya status administrasi)",
          ", ".join(sorted(healths)))
    expired_wrong = [p["type"] for p in data["permits"]
                     if p["status"] == "approved" and p.get("days_to_expiry") is not None
                     and p["days_to_expiry"] < 0 and p["health"] != "expired"]
    check(not expired_wrong,
          "izin 'disetujui' yang tanggalnya lewat dilaporkan KEDALUWARSA",
          ", ".join(expired_wrong))
    no_expiry = [p for p in data["permits"] if not p["expiry_known"]]
    check(bool(no_expiry) and all(p["expiry_at"] in (None, "") for p in no_expiry),
          "izin tanpa tanggal berlaku ditandai 'belum dicatat' (bukan aman selamanya)",
          f"{len(no_expiry)} izin")
    lst = requests.get(f"{BASE}/permits", headers=pm, timeout=30).json()
    check("no_expiry_data" in lst["summary"] and "expiring" in lst["summary"],
          "ringkasan izin menyebut menjelang kedaluwarsa & masa berlaku belum dicatat")
    check(all(p.get("scope") for p in lst["data"]),
          "semua izin (termasuk data lama) punya cakupan objek setelah migrasi")
    # izin wajib: memblokir HANYA bila didaftarkan admin
    before = requests.get(f"{BASE}/build/unit/{unit['unit_id']}/readiness", headers=pm,
                          timeout=30).json()["data"]
    requests.put(f"{BASE}/settings/permit.block_build_without", headers=owner,
                 json={"value": ["ANDALALIN"], "reason": "Uji gate Fase 46."}, timeout=30)
    try:
        after = requests.get(f"{BASE}/build/unit/{unit['unit_id']}/readiness", headers=pm,
                             timeout=30).json()["data"]
        check("permit_missing" not in [x["code"] for x in before["reasons"]]
              and any(x["code"] == "permit_missing" and x["severity"] == "blocker"
                      for x in after["reasons"]),
              "daftar izin wajib kosong = tidak memblokir; diisi = memblokir (uji negatif)")
    finally:
        requests.post(f"{BASE}/settings/permit.block_build_without/reset",
                      headers=owner, timeout=30)
    # peringatan masa berlaku: notifikasi + tugas
    db.permits.update_many({"org_id": "org-sipro"},
                           {"$set": {"expiry_notified_on": None}})
    scan = requests.post(f"{BASE}/permits/alerts/scan", headers=pm, timeout=60)
    check(scan.status_code == 200 and scan.json()["data"]["alerts"] > 0,
          "POST /permits/alerts/scan mengirim peringatan izin",
          f"{scan.status_code} {scan.text[:70]}")
    tasks = db.tasks.count_documents({"org_id": "org-sipro", "meta.permit_id": {"$ne": None}})
    notif = db.notifications.count_documents({"org_id": "org-sipro", "type": "permit"})
    check(tasks > 0, "peringatan izin melahirkan TUGAS Work Hub (bukan hanya pesan)",
          f"{tasks} tugas")
    check(notif > 0, "peringatan izin melahirkan notifikasi in-app", f"{notif} notifikasi")
    for path, params in (("/build/board/units", {}), ("/permits", {}),
                         ("/permits/coverage", {"unit_id": unit["unit_id"]})):
        r = requests.get(f"{BASE}{path}", headers=sales, params=params, timeout=30)
        check(r.status_code == 403, f"sales ditolak sopan di {path} (403)", str(r.status_code))
    r = requests.get(f"{BASE}/build/board/units", timeout=30)
    check(r.status_code == 401, "tanpa token → 401", str(r.status_code))


def main():
    print("=" * 78)
    print("GATE FASE 46 — Konsolidasi Proyek & Konstruksi (papan unit, gerbang, izin)")
    print("=" * 78)
    pm = login("pm@sipro.co.id")
    owner = login("owner@sipro.co.id")
    site = login("site@sipro.co.id")
    sales = login("sales@sipro.co.id")
    audit_ia()
    audit_poc()
    rows = audit_board(pm)
    if rows:
        audit_gate(pm, owner, site, rows)
        audit_permits(pm, owner, sales, rows)
    print("\n" + "-" * 58)
    if fails:
        print(f"HASIL verify_build_hub: {len(fails)} FAIL → " + "; ".join(fails[:6]))
        print("GATE FASE 46 GAGAL")
        return 1
    print("HASIL verify_build_hub: SEMUA PASS")
    print("GATE FASE 46 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
