# Analysis and figures for research/2026-11-cosmic-rays/README.md
#
#   python analyze.py <dir with monitor_*.csv and events_*.csv> [--main-end 2026-11-09T19:30:00]
#
# Two runs on the same desk, same tubes, same 360 V:
#   before  firmware 1.4.0, 2026-11-07 11:01 .. 11-09 08:45 (45.7 h). Repeat pulses inflate the counts;
#           they are merged here (hits closer than CLUSTER_S on the logger clock count once)
#   main    firmware 1.6.0 (dead time 200 us, evenly spaced HV pulses), 2026-11-09 09:30 ..
# Writes results.json (every number in the README), data/*.csv and figures/*_{light,dark}.png.
# Needs numpy, scipy, matplotlib.
import argparse
import collections
import csv
import datetime as dt
import glob
import json
import math
import os

import numpy as np
from scipy import stats

RUNS = {
    "before": ("2026-11-07T11:01:20", "2026-11-09T08:45:30"),
    "main": ("2026-11-09T09:30:50", None),
}
CLUSTER_S = 0.010   # one discharge on the logger clock (ms resolution)
WIN_US = 100        # coincidence window of the firmware (win 100)
DT_MAX_US = 5000    # widest pair the firmware reports with "dt on"

# tubes (J321), measured: outer length 7 cm, diameter 1 cm, centres about 12 mm apart, one above the other
TUBE_D_CM, TUBE_SEP_CM = 1.0, 1.2
TUBE_L_CM = (5.0, 7.0)   # the sensitive length lies somewhere below the glass length
I0_REF = 70e-4           # vertical muon intensity, cm^-2 s^-1 sr^-1 (PDG, p > 1 GeV/c)

# muon and relativity
TAU_US = 2.197
MMU_GEV = 0.10566
E_GEV = 4.0              # typical energy of muons at sea level
H_KM = 15.0              # height where most muons are made
C_M_PER_US = 299.792458


def load(d, start, end):
    ev, rows = [], []
    for f in sorted(glob.glob(os.path.join(d, "events_*.csv"))):
        for x in csv.DictReader(open(f)):
            if x["time"] >= start and (end is None or x["time"] < end):
                ev.append((float(x["unix"]), x["kind"], x["value"]))
    for f in sorted(glob.glob(os.path.join(d, "monitor_*.csv"))):
        for x in csv.DictReader(open(f)):
            if x["time"] >= start and (end is None or x["time"] < end):
                rows.append(x)
    return ev, rows


def clusters(ev):
    out, cur = [], [ev[0]]
    for e in ev[1:]:
        if e[0] - cur[-1][0] < CLUSTER_S:
            cur.append(e)
        else:
            out.append(cur)
            cur = [e]
    out.append(cur)
    return out


def nhit(c, v):
    return sum(e[1] == "hit" and e[2] == v for e in c)


def analyse(ev, rows):
    """Everything for one run. Counts are per discharge (merged), raw counts are kept too."""
    cl = clusters(ev)
    t0, t1 = ev[0][0], ev[-1][0]
    T = t1 - t0
    R = {"start": dt.datetime.fromtimestamp(t0).isoformat(timespec="seconds"),
         "end": dt.datetime.fromtimestamp(t1).isoformat(timespec="seconds"),
         "hours": round(T / 3600, 2)}
    tt = [dt.datetime.fromisoformat(x["time"]) for x in rows]
    R["record_gaps_over_25s"] = sum((b - a).total_seconds() > 25 for a, b in zip(tt, tt[1:]))
    hv = np.array([float(x["hv_V"]) for x in rows])
    R["hv"] = {"min": float(hv.min()), "max": float(hv.max()), "mean": round(float(hv.mean()), 1),
               "sd": round(float(hv.std()), 2)}
    R["raw"] = {v: sum(nhit(c, v) for c in cl) for v in "ABC"}
    N = {v: sum(nhit(c, v) > 0 for c in cl) for v in "ABC"}
    R["count"] = N
    R["rate_per_min"] = {v: round(N[v] / T * 60, 3) for v in "ABC"}
    R["c_rate_err"] = round(math.sqrt(N["C"]) / T * 60, 3)
    R["multi_c_per_hour"] = round(sum(nhit(c, "C") >= 2 for c in cl) / T * 3600, 2)
    R["repeat_per_hour"] = round(sum((nhit(c, "A") >= 2) + (nhit(c, "B") >= 2) for c in cl) / T * 3600, 1)
    rA, rB = N["A"] / T, N["B"] / T
    R["repeat_chance_per_hour"] = round((rA * rA + rB * rB) * CLUSTER_S * 3600, 1)

    # time differences, first pair of each discharge
    dts = []
    for c in cl:
        d = [int(e[2]) for e in c if e[1] == "dt"]
        dts += d[:1]
    dts = np.array(dts)
    inwin = dts[np.abs(dts) <= WIN_US]
    R["dt"] = {"in_window": len(inwin), "median_us": float(np.median(inwin)),
               "share_9_17_pct": round(float(np.mean((inwin >= 9) & (inwin <= 17)) * 100), 1),
               "share_m24_m15_pct": round(float(np.mean((inwin >= -24) & (inwin <= -15)) * 100), 1)}
    # the two peaks: A's interrupt serviced first (dt > 0) or B's (dt < 0); means within 30 us of zero
    pos, neg = inwin[(inwin > 0) & (inwin <= 30)], inwin[(inwin < 0) & (inwin >= -30)]
    R["dt"].update({"pos_mean_us": round(float(pos.mean()), 1) if len(pos) else None,
                    "neg_mean_us": round(float(neg.mean()), 1) if len(neg) else None,
                    "neg_share_pct": round(float(np.mean(inwin < 0) * 100), 1)})
    # accidental floor: pairs 0.1..5 ms apart, both signs
    far = np.abs(dts[(np.abs(dts) > WIN_US) & (np.abs(dts) <= DT_MAX_US)])
    width_s = 2 * (DT_MAX_US - WIN_US) * 1e-6
    R["accidental"] = {"pairs_0p1_5ms": int(len(far)), "expected_0p1_5ms": round(width_s * rA * rB * T, 1),
                       "in_window_expected": round(2 * WIN_US * 1e-6 * rA * rB * T, 2),
                       "in_window_share_pct": round(2 * WIN_US * 1e-6 * rA * rB * T / N["C"] * 100, 3)}
    R["delayed_D"] = sum(float(x["D"]) for x in rows)
    R["delayed_expected"] = round(2 * R["accidental"]["in_window_expected"], 2)

    Ct = np.array([c[0][0] for c in cl if nhit(c, "C")])
    iv = np.diff(Ct)
    m = float(iv.mean())
    R["interval"] = {"n": len(iv), "mean_s": round(m, 1), "median_s": round(float(np.median(iv)), 1),
                     "exp_median_s": round(m * math.log(2), 1), "max_s": round(float(iv.max()), 1),
                     "p_max": round(float(len(iv) * math.exp(-iv.max() / m)), 2),
                     "ks_p": round(float(stats.kstest(iv, "expon", args=(0, m)).pvalue), 2)}
    nm = int(T // 60)
    pm = collections.Counter(int((t - t0) // 60) for t in Ct if t - t0 < nm * 60)
    counts = np.array([pm.get(i, 0) for i in range(nm)])
    lam = float(counts.mean())
    kmax = int(counts.max())
    obs = np.array([int(np.sum(counts == k)) for k in range(kmax + 1)])
    exp = np.array([nm * stats.poisson.pmf(k, lam) for k in range(kmax + 1)])
    # chi-square goodness of fit, tail merged so every expected bin is >= 5
    o, e = list(obs), list(exp)
    while len(e) > 2 and e[-1] < 5:
        o[-2] += o.pop()
        e[-2] += e.pop()
    e[-1] += nm - sum(exp)  # the tail beyond kmax
    chi = float(sum((a - b) ** 2 / b for a, b in zip(o, e)))
    R["per_minute"] = {"minutes": nm, "mean": round(lam, 3), "var": round(float(counts.var()), 3),
                       "obs": obs.tolist(), "exp": [round(x, 1) for x in exp],
                       "chi2": round(chi, 1), "dof": len(o) - 2, "p": round(float(stats.chi2.sf(chi, len(o) - 2)), 2)}
    # hourly
    h0 = dt.datetime.fromtimestamp(t0).replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    h1 = dt.datetime.fromtimestamp(t1).replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
    hours = []
    while h0 <= h1:
        hours.append(h0)
        h0 += dt.timedelta(hours=1)
    H = collections.Counter(dt.datetime.fromtimestamp(t).replace(minute=0, second=0, microsecond=0) for t in Ct)
    hc = np.array([H[h] for h in hours])
    if len(hc) > 1:
        chi = float(((hc - hc.mean()) ** 2).sum() / hc.mean())
        R["hourly_c"] = {"hours": len(hc), "mean": round(float(hc.mean()), 1), "min": int(hc.min()), "max": int(hc.max()),
                         "chi2": round(chi, 1), "dof": len(hc) - 1, "p": round(float(stats.chi2.sf(chi, len(hc) - 1)), 2)}
        # the same with every coincidence the firmware reported (repeats not merged)
        Hr = collections.Counter(dt.datetime.fromtimestamp(c[0][0]).replace(minute=0, second=0, microsecond=0)
                                 for c in cl for _ in range(nhit(c, "C")))
        hr = np.array([Hr[h] for h in hours])
        chi = float(((hr - hr.mean()) ** 2).sum() / hr.mean())
        R["hourly_c_raw"] = {"chi2": round(chi, 1), "dof": len(hr) - 1, "p": round(float(stats.chi2.sf(chi, len(hr) - 1)), 3)}
    R["rate_per_min_raw"] = {v: round(R["raw"][v] / T * 60, 3) for v in "ABC"}
    return R, dict(Ct=Ct, iv=iv, obs=obs, exp=exp, dts=dts, hours=hours, hourly=hc, rA=rA, rB=rB, T=T)


# ---- geometry: Monte Carlo of lines through both tubes ----
def through(p, d, yc, zc, r, L):
    """Does the line p + t d cross the finite cylinder along x centred at (0, yc, zc)?"""
    py, pz = p[:, 1] - yc, p[:, 2] - zc
    a = d[:, 1] ** 2 + d[:, 2] ** 2
    b = 2 * (py * d[:, 1] + pz * d[:, 2])
    c = py ** 2 + pz ** 2 - r * r
    disc = b * b - 4 * a * c
    ok = (disc > 0) & (a > 1e-12)
    sq = np.sqrt(np.where(ok, disc, 0))
    a2 = np.where(ok, 2 * a, 1)
    x1 = p[:, 0] + (-b - sq) / a2 * d[:, 0]
    x2 = p[:, 0] + (-b + sq) / a2 * d[:, 0]
    return ok & (np.maximum(x1, x2) > -L / 2) & (np.minimum(x1, x2) < L / 2)


def geometry(L, tilt_deg=0.0, n=2_000_000, reps=8, seed=1):
    """G = integral of A(theta, phi) cos^2(theta) dOmega [cm^2 sr], so that the rate is I0 * G.
    Lines cross the plane z = 0 with directions weighted by cos^2 (intensity) x cos (flux through the plane).
    tilt_deg turns the pair about the tube axis: 0 = one above the other, 90 = side by side.
    Also returns the zenith angles of the accepted lines and a few of them (point, direction) to draw."""
    rng = np.random.default_rng(seed)
    r = TUBE_D_CM / 2
    yc = TUBE_SEP_CM / 2 * math.sin(math.radians(tilt_deg))
    zc = TUBE_SEP_CM / 2 * math.cos(math.radians(tilt_deg))
    gs, th, sample = [], [], None
    for _ in range(reps):
        cz = rng.random(n) ** 0.25           # pdf of cos(theta) proportional to cos^3
        sz = np.sqrt(1 - cz * cz)
        ph = rng.random(n) * 2 * np.pi
        d = np.stack([sz * np.cos(ph), sz * np.sin(ph), -cz], 1)
        wx, wy = L / 2 + 6, 3 * TUBE_SEP_CM + 6
        p = np.stack([(rng.random(n) * 2 - 1) * wx, (rng.random(n) * 2 - 1) * wy, np.zeros(n)], 1)
        hit = through(p, d, +yc, +zc, r, L) & through(p, d, -yc, -zc, r, L)
        gs.append(hit.mean() * 4 * wx * wy * math.pi / 2)   # integral of cos^3 over the hemisphere = pi/2
        th.append(np.degrees(np.arccos(cz[hit])))
        if sample is None:
            sample = (p[hit][:60], d[hit][:60], p[~hit][:4000], d[~hit][:4000])
    return float(np.mean(gs)), float(np.std(gs) / math.sqrt(reps)), np.concatenate(th), sample


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--main-end", default=None)
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    a = ap.parse_args()
    out = {}
    raw = {}
    for name, (s, e) in RUNS.items():
        e = a.main_end if name == "main" and a.main_end else e
        ev, rows = load(a.data, s, e)
        out[name], raw[name] = analyse(ev, rows)

    # geometry and the predicted rate
    geo = {}
    th_all = None
    for L in TUBE_L_CM:
        g, ge, th, sample = geometry(L)
        gs, _, _, _ = geometry(L, tilt_deg=90)
        geo[f"L{L:g}"] = {"G_cm2sr": round(g, 3), "G_err": round(ge, 3), "pred_per_min": round(I0_REF * g * 60, 2),
                          "side_by_side_G": round(gs, 3), "stacked_over_side": round(g / gs, 2),
                          "theta_mean_deg": round(float(th.mean()), 1), "theta_median_deg": round(float(np.median(th)), 1)}
        if L == TUBE_L_CM[-1]:
            th_all, rays = th, sample
    rm = out["main"]["rate_per_min"]["C"] / 60
    geo["I0_measured_range"] = [round(rm / geo[f"L{L:g}"]["G_cm2sr"] * 1e4, 1) for L in TUBE_L_CM]   # m^-2 s^-1 sr^-1
    out["geometry"] = geo
    out["tube"] = {"d_mm": TUBE_D_CM * 10, "sep_mm": TUBE_SEP_CM * 10, "glass_mm": TUBE_L_CM[-1] * 10,
                   "transit_ns": round(TUBE_SEP_CM * 1e-2 / 2.998e8 * 1e9, 3)}
    # turning the pair about the tube axis: the prediction for the zenith-angle experiment
    g0 = geo[f"L{TUBE_L_CM[0]:g}"]["G_cm2sr"]
    out["tilt"] = {f"{a}": round(geometry(TUBE_L_CM[0], tilt_deg=a, reps=4)[0] / g0, 3) for a in (0, 15, 30, 45, 60, 75, 90)}

    # relativity numbers for the explanation
    gamma = E_GEV / MMU_GEV
    beta = math.sqrt(1 - 1 / gamma ** 2)
    ctau = C_M_PER_US * TAU_US
    out["relativity"] = {"gamma": round(gamma, 1), "ctau_m": round(ctau, 1), "decay_length_km": round(gamma * beta * ctau / 1000, 1),
                         "survive_with": round(math.exp(-H_KM * 1000 / (gamma * beta * ctau)), 2),
                         "survive_without": f"{math.exp(-H_KM * 1000 / ctau):.1e}"}

    # future work: statistical error for one day at the main rate
    per_day = out["main"]["rate_per_min"]["C"] * 1440
    out["per_day"] = {"count": round(per_day), "rel_err_pct": round(100 / math.sqrt(per_day), 2)}

    os.makedirs(os.path.join(a.out, "data"), exist_ok=True)
    for name in RUNS:
        with open(os.path.join(a.out, "data", f"coincidences_{name}.csv"), "w") as f:
            w = csv.writer(f)
            w.writerow(["unix_s"])
            for t in raw[name]["Ct"]:
                w.writerow([f"{t:.3f}"])
        with open(os.path.join(a.out, "data", f"hourly_{name}.csv"), "w") as f:
            w = csv.writer(f)
            w.writerow(["hour_start_jst", "coincidences"])
            for h, c in zip(raw[name]["hours"], raw[name]["hourly"]):
                w.writerow([h.isoformat(), int(c)])
    json.dump(out, open(os.path.join(a.out, "results.json"), "w"), indent=1, ensure_ascii=False, default=float)
    figures(a.out, out, raw, th_all, rays)
    from paper_figures import paper_figures
    paper_figures(a.out, out, raw, th_all, rays, out["tube"], list(RUNS))
    print(json.dumps(out, indent=1, ensure_ascii=False, default=float))


# ---------------- figures ----------------
THEMES = {
    "light": dict(surface="#fcfcfb", text="#0b0b0b", text2="#52514e", grid="#e4e3df", band="#ecebe7",
                  s1="#2a78d6", s2="#eb6834", s3="#1baf7a"),
    "dark": dict(surface="#1a1a19", text="#f0efec", text2="#c3c2b7", grid="#3a3936", band="#2a2a28",
                 s1="#3987e5", s2="#d95926", s3="#199e70"),
}


def figures(out_dir, res, raw, th, rays):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.ticker import FuncFormatter, LogLocator, NullLocator
    p = os.path.expanduser("~/Library/Fonts/NotoSansJP-VariableFont_wght.ttf")
    if os.path.exists(p):
        font_manager.fontManager.addfont(p)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=p).get_name()
    plt.rcParams["mathtext.fontset"] = "dejavusans"
    os.makedirs(os.path.join(out_dir, "figures"), exist_ok=True)
    M = raw["main"]

    for name, T in THEMES.items():
        def new(h=3.6, top=0.96):
            fig, ax = plt.subplots(figsize=(8, h))
            fig.subplots_adjust(left=0.1, right=0.97, top=top, bottom=0.15)
            style(ax)
            return fig, ax

        def style(ax):
            ax.set_facecolor(T["surface"])
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
            for s in ("left", "bottom"):
                ax.spines[s].set_color(T["grid"])
            ax.tick_params(colors=T["text2"], labelsize=9, length=0)
            ax.yaxis.grid(True, color=T["grid"], linewidth=0.8)
            ax.set_axisbelow(True)

        def labels(ax, x, y):
            ax.set_xlabel(x, color=T["text2"], fontsize=9)
            ax.set_ylabel(y, color=T["text2"], fontsize=9)

        def plain_log(ax):
            ax.yaxis.set_major_locator(LogLocator(base=10))
            ax.yaxis.set_minor_locator(NullLocator())
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}" if v >= 1 else f"{v:g}"))

        def title(fig, t, sub):
            pass  # captions are in the README, as in a paper

        def legend(ax, **kw):
            lg = ax.legend(frameon=False, fontsize=9, **kw)
            for t_ in lg.get_texts():
                t_.set_color(T["text"])

        def save(fig, f):
            fig.patch.set_facecolor(T["surface"])
            fig.savefig(os.path.join(out_dir, "figures", f"{f}_{name}.png"), dpi=200, facecolor=T["surface"])
            plt.close(fig)

        # 1. Poisson: counts per minute
        r = res["main"]["per_minute"]
        obs, exp = np.array(r["obs"]), np.array(r["exp"])
        fig, ax = new()
        title(fig, "1 分間に数えた回数の分布",
              f"棒が測った分の数（{r['minutes']:,} 分）、点がポアソン分布 P(k) の見込み（λ = {r['mean']}）")
        k = np.arange(len(obs))
        ax.bar(k, obs, width=0.7, color=T["s1"], linewidth=0, label="測った分の数")
        ax.plot(k, exp, linestyle="none", marker="o", markersize=7, color=T["text"],
                markeredgecolor=T["surface"], markeredgewidth=2, label="ポアソン分布の見込み")
        for i in k:
            ax.text(i, max(obs[i], exp[i]) + obs.max() * 0.035, str(obs[i]), ha="center", va="bottom", color=T["text2"], fontsize=8)
        ax.set_xticks(k)
        ax.set_ylim(0, obs.max() * 1.18)
        labels(ax, "1 分間の回数 k", "分の数")
        legend(ax, loc="upper right")
        save(fig, "poisson")

        # 2. intervals
        iv, m = M["iv"], res["main"]["interval"]["mean_s"]
        fig, ax = new()
        title(fig, "次に光るまでの間隔",
              f"棒が測った回数、線が指数分布 p(Δt) = r e^(−rΔt) の見込み（平均 {m} 秒）")
        edges = np.arange(0, max(200, iv.max() + 10), 10)
        cnt, _ = np.histogram(iv, edges)
        ax.bar(edges[:-1] + 5, cnt, width=8.6, color=T["s1"], linewidth=0, label="測った間隔")
        xs = np.linspace(0, edges[-1], 300)
        ax.plot(xs, len(iv) * 10 / m * np.exp(-xs / m), color=T["text"], linewidth=1.6, label="指数分布")
        ax.set_yscale("log")
        ax.set_ylim(0.5, cnt.max() * 2)
        plain_log(ax)
        labels(ax, "間隔 Δt（秒）", "回数（10 秒ごと、対数）")
        legend(ax, loc="upper right")
        save(fig, "intervals")

        # 3. coincidence spectrum 0..5 ms with the accidental floor
        d = np.abs(M["dts"])
        edges = np.arange(0, DT_MAX_US + 1, 100)
        cnt, _ = np.histogram(d, edges)
        floor = 2 * 100e-6 * M["rA"] * M["rB"] * M["T"]
        fig, ax = new()
        title(fig, "2 本の管の時間差（0〜5 ms）",
              "最初の 0.1 ms に本物の同時が集まり、その外は偶然の重なりだけになる。線は式 2Δt·R_A·R_B·T の見込み")
        ax.bar(edges[1:2] - 50, cnt[:1], width=86, color=T["s1"], linewidth=0, label="同時の窓（|Δt| ≤ 100 µs）")
        ax.bar(edges[1:-1] + 50, cnt[1:], width=86, color=T["s2"], linewidth=0, label="窓の外（偶然の重なり）")
        ax.axhline(floor, color=T["text"], linewidth=1.4, linestyle="--", label=f"偶然の見込み {floor:.2f} 回 / 100 µs")
        ax.set_yscale("log")
        ax.set_ylim(0.1, cnt[0] * 3)
        plain_log(ax)
        labels(ax, "時間差 |Δt|（µs）", "組の数（100 µs ごと、対数）")
        legend(ax, loc="upper right")
        save(fig, "coincidence")

        # 4. zenith angle of the muons the pair accepts
        fig, ax = new()
        title(fig, "2 本を両方とも通るミューオンの天頂角",
              "管の形と cos²θ の強度分布からのモンテカルロ。真上から 0°、真横が 90°")
        e = np.arange(0, 91, 2.5)
        c, _ = np.histogram(th, e)
        ax.bar(e[:-1] + 1.25, c / c.sum() * 100, width=2.1, color=T["s1"], linewidth=0)
        med = float(np.median(th))
        ax.axvline(med, color=T["text"], linewidth=1.2, linestyle="--")
        ax.text(med + 1, ax.get_ylim()[1] * 0.92, f"中央値 {med:.0f}°", color=T["text"], fontsize=9)
        ax.plot([], [])
        ax.set_xlim(0, 90)
        labels(ax, "天頂角 θ（度）", "割合（%、2.5° ごと）")
        save(fig, "zenith")

        # 5. time difference inside the window
        fig, ax = new()
        title(fig, "2 本の管の時間差（±100 µs の中）",
              "粒子は 2 本を数 ns で通り抜ける。見えている差は、管の放電とマイコンの処理の順番から来る")
        e = np.arange(-100, 101, 2)
        c, _ = np.histogram(M["dts"], e)
        ax.bar(e[:-1] + 1, c, width=1.7, color=T["s1"], linewidth=0)
        ax.set_yscale("log")
        ax.set_ylim(0.7, c.max() * 3)
        plain_log(ax)
        labels(ax, "時間差 Δt = t_B − t_A（µs）", "回数（2 µs ごと、対数）")
        save(fig, "dt")

        # 6. the whole record: hourly rate of both runs on one time axis
        fig, ax = new(h=3.6)
        title(fig, "1 時間ごとの 2 本同時（2 回の記録）",
              "帯は、ランダムな揺れで 95% が入る範囲。1.4.0 の記録は二重カウントを除いた値")
        for run, col, lab in (("before", T["s2"], "ファーム 1.4.0（45.7 時間）"), ("main", T["s1"], "ファーム 1.6.0")):
            hs, hc = raw[run]["hours"], raw[run]["hourly"]
            if len(hs):
                ax.plot(hs, hc, color=col, linewidth=2, marker="o", markersize=3, label=lab)
        allc = np.concatenate([raw[r]["hourly"] for r in RUNS])
        mu = allc.mean()
        ax.axhspan(stats.poisson.ppf(0.025, mu), stats.poisson.ppf(0.975, mu), color=T["band"], linewidth=0)
        ax.axhline(mu, color=T["text2"], linewidth=1)
        import matplotlib.dates as mdates
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%-m/%-d %-H時"))
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12]))
        labels(ax, "", "回数 / 時")
        legend(ax, loc="lower left", ncol=2)
        save(fig, "hourly")

        # 7. the detector: cross-section and side view, with lines that pass both tubes
        from matplotlib.patches import Circle, Rectangle
        ph, dh, pm, dm = rays
        fig, axs = plt.subplots(1, 2, figsize=(8, 3.6), gridspec_kw=dict(width_ratios=[1, 1.7], wspace=0.08))
        fig.subplots_adjust(left=0.02, right=0.98, top=0.98, bottom=0.07)
        title(fig, "2 本の管と、両方を通る粒子の道すじ",
              f"モンテカルロで選んだ線。青が 2 本とも通る（数える）、灰が片方だけか素通り。管 φ{TUBE_D_CM * 10:.0f} mm、中心間 {TUBE_SEP_CM * 10:.0f} mm")
        r = TUBE_D_CM / 2
        L = TUBE_L_CM[-1]
        for ax, (i, lab) in zip(axs, ((1, "(a) 断面（管の軸の方向から）"), (0, "(b) 側面"))):
            ax.set_facecolor(T["surface"])
            ax.set_aspect("equal")
            ax.axis("off")
            ax.text(0.5, -0.02, lab, transform=ax.transAxes, ha="center", va="top", color=T["text2"], fontsize=9)
            for P, Dd, col, lw, al in ((pm[:25], dm[:25], T["text2"], 0.8, 0.35), (ph, dh, T["s1"], 1.1, 0.8)):
                for p0, d0 in zip(P, Dd):
                    tt = np.array([-3.2, 3.2]) / max(abs(d0[2]), 0.05)
                    xs, zs = p0[i] + tt * d0[i], p0[2] + tt * d0[2]
                    ax.plot(xs, zs, color=col, linewidth=lw, alpha=al)
            for s in (+1, -1):
                zc = s * TUBE_SEP_CM / 2
                if i == 1:
                    ax.add_patch(Circle((0, zc), r, facecolor=T["band"], edgecolor=T["text"], linewidth=1.4, zorder=5))
                else:
                    ax.add_patch(Rectangle((-L / 2, zc - r), L, 2 * r, facecolor=T["band"], edgecolor=T["text"], linewidth=1.4, zorder=5))
            w = 2.2 if i == 1 else L / 2 + 1.2
            ax.set_xlim(-w, w)
            ax.set_ylim(-2.2, 2.2)
        save(fig, "detector")

        # 8. prediction for turning the pair
        tl = res["tilt"]
        ang = [int(k) for k in tl]
        fig, ax = new(h=3.2)
        title(fig, "予言: 2 本の並びを傾けたときの回数",
              "真上下に重ねた（0°）ときを 1 とした割合。90° は横に並べたとき。cos²θ の分布と管の形からの計算")
        ax.plot(ang, [tl[str(a_)] for a_ in ang], color=T["s1"], linewidth=2, marker="o", markersize=6)
        for a_ in ang:
            ax.text(a_, tl[str(a_)] + 0.05, f"{tl[str(a_)]:.2f}", ha="center", color=T["text2"], fontsize=8)
        ax.set_xticks(ang)
        ax.set_ylim(0, 1.15)
        labels(ax, "傾けた角度（度）", "回数の割合")
        save(fig, "tilt")


if __name__ == "__main__":
    main()
