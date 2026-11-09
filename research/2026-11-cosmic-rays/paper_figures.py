# Vector figures for the paper (paper/ja and paper/en), drawn from the same results as the README figures.
# Single-column figures are 3.4 in wide, full-width ones 7.0 in; text at 8 pt; fonts embedded (TrueType).
import os

import numpy as np
from scipy import stats

INK, INK2, GRID, BAND = "#111111", "#555555", "#dddddd", "#ececec"
BLUE, ORANGE = "#2a78d6", "#eb6834"

L = {
    "ja": {
        "k": "1 分間の計数 $k$", "minutes": "分の数", "measured": "実測", "poisson": "ポアソン分布 (1)",
        "interval": "到来間隔 $\\Delta t$ [s]", "count10": "事象数 / 10 s", "expo": "指数分布 (2)",
        "absdt": "$|\\Delta t|$ [$\\mu$s]", "pairs100": "組の数 / 100 $\\mu$s", "window": "同時の窓",
        "outside": "窓の外", "acc": "式 (3) の予測",
        "dt": "$\\Delta t = t_B - t_A$ [$\\mu$s]", "count2": "事象数 / 2 $\\mu$s",
        "hour_y": "同時計数 / h", "runA": "観測 A（1.4.0、補正後）", "runB": "観測 B（1.6.0）",
        "theta": "天頂角 $\\theta$ [deg]", "share": "割合 [% / 2.5°]", "median": "中央値",
        "tilt": "傾き $\\alpha$ [deg]", "ratio": "計数率の比",
        "section": "(a) 断面", "side": "(b) 側面", "date": "%-m/%-d %-H時",
    },
    "en": {
        "k": "Counts per minute $k$", "minutes": "Minutes", "measured": "Measured", "poisson": "Poisson, Eq. (1)",
        "interval": "Waiting time $\\Delta t$ [s]", "count10": "Events / 10 s", "expo": "Exponential, Eq. (2)",
        "absdt": "$|\\Delta t|$ [$\\mu$s]", "pairs100": "Pairs / 100 $\\mu$s", "window": "Coincidence window",
        "outside": "Outside the window", "acc": "Eq. (3)",
        "dt": "$\\Delta t = t_B - t_A$ [$\\mu$s]", "count2": "Events / 2 $\\mu$s",
        "hour_y": "Coincidences / h", "runA": "Run A (1.4.0, corrected)", "runB": "Run B (1.6.0)",
        "theta": "Zenith angle $\\theta$ [deg]", "share": "Fraction [% / 2.5°]", "median": "median",
        "tilt": "Tilt $\\alpha$ [deg]", "ratio": "Rate ratio",
        "section": "(a) Cross section", "side": "(b) Side view", "date": "%b %-d %H:00",
    },
}


def paper_figures(out_dir, res, raw, th, rays, tube, runs):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import Circle, Rectangle
    from matplotlib.ticker import FuncFormatter, LogLocator, NullLocator
    p = os.path.expanduser("~/Library/Fonts/NotoSansJP-VariableFont_wght.ttf")
    if os.path.exists(p):
        font_manager.fontManager.addfont(p)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=p).get_name()
    plt.rcParams.update({"pdf.fonttype": 42, "font.size": 8, "axes.labelsize": 8, "legend.fontsize": 7,
                         "xtick.labelsize": 7, "ytick.labelsize": 7, "mathtext.fontset": "dejavusans",
                         "axes.edgecolor": INK2, "axes.linewidth": 0.6, "xtick.color": INK2, "ytick.color": INK2,
                         "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.major.size": 2.5, "ytick.major.size": 2.5})
    M = raw["main"]
    d_cm, sep_cm, glass_cm = tube["d_mm"] / 10, tube["sep_mm"] / 10, tube["glass_mm"] / 10

    for lang, T in L.items():
        od = os.path.join(out_dir, "paper", "figures", lang)
        os.makedirs(od, exist_ok=True)

        def new(w=3.4, h=2.2):
            fig, ax = plt.subplots(figsize=(w, h))
            fig.subplots_adjust(left=0.16 if w < 5 else 0.08, right=0.97, top=0.96, bottom=0.2 if w < 5 else 0.2)
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
            ax.yaxis.grid(True, color=GRID, linewidth=0.5)
            ax.set_axisbelow(True)
            return fig, ax

        def plain_log(ax):
            ax.yaxis.set_major_locator(LogLocator(base=10))
            ax.yaxis.set_minor_locator(NullLocator())
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}" if v >= 1 else f"{v:g}"))

        def save(fig, name):
            fig.savefig(os.path.join(od, f"{name}.pdf"))
            plt.close(fig)

        # Poisson
        r = res["main"]["per_minute"]
        obs, exp = np.array(r["obs"]), np.array(r["exp"])
        fig, ax = new()
        k = np.arange(len(obs))
        ax.bar(k, obs, width=0.7, color=BLUE, linewidth=0, label=T["measured"])
        ax.plot(k, exp, "o", ms=4, color=INK, mec="white", mew=1, label=T["poisson"])
        ax.set_xticks(k)
        ax.set_xlabel(T["k"])
        ax.set_ylabel(T["minutes"])
        ax.legend(frameon=False)
        save(fig, "poisson")

        # waiting times
        iv, m = M["iv"], res["main"]["interval"]["mean_s"]
        fig, ax = new()
        e = np.arange(0, max(200, iv.max() + 10), 10)
        c, _ = np.histogram(iv, e)
        ax.bar(e[:-1] + 5, c, width=8.6, color=BLUE, linewidth=0, label=T["measured"])
        xs = np.linspace(0, e[-1], 200)
        ax.plot(xs, len(iv) * 10 / m * np.exp(-xs / m), color=INK, lw=1, label=T["expo"])
        ax.set_yscale("log")
        ax.set_ylim(0.5, c.max() * 2)
        plain_log(ax)
        ax.set_xlabel(T["interval"])
        ax.set_ylabel(T["count10"])
        ax.legend(frameon=False)
        save(fig, "intervals")

        # coincidence spectrum
        d = np.abs(M["dts"])
        e = np.arange(0, 5001, 100)
        c, _ = np.histogram(d, e)
        floor = 2 * 100e-6 * M["rA"] * M["rB"] * M["T"]
        fig, ax = new()
        ax.bar(e[:1] + 50, c[:1], width=86, color=BLUE, linewidth=0, label=T["window"])
        ax.bar(e[1:-1] + 50, c[1:], width=86, color=ORANGE, linewidth=0, label=T["outside"])
        ax.axhline(floor, color=INK, lw=1, ls="--", label=T["acc"])
        ax.set_yscale("log")
        ax.set_ylim(0.1, c[0] * 3)
        plain_log(ax)
        ax.set_xlabel(T["absdt"])
        ax.set_ylabel(T["pairs100"])
        ax.legend(frameon=False, loc="upper right")
        save(fig, "coincidence")

        # time difference inside the window
        fig, ax = new()
        e = np.arange(-100, 101, 2)
        c, _ = np.histogram(M["dts"], e)
        ax.bar(e[:-1] + 1, c, width=1.7, color=BLUE, linewidth=0)
        ax.set_yscale("log")
        ax.set_ylim(0.7, c.max() * 3)
        plain_log(ax)
        ax.set_xlabel(T["dt"])
        ax.set_ylabel(T["count2"])
        save(fig, "dt")

        # zenith angle
        fig, ax = new()
        e = np.arange(0, 91, 2.5)
        c, _ = np.histogram(th, e)
        ax.bar(e[:-1] + 1.25, c / c.sum() * 100, width=2.1, color=BLUE, linewidth=0)
        med = float(np.median(th))
        ax.axvline(med, color=INK, lw=0.8, ls="--")
        ax.text(med + 1.5, ax.get_ylim()[1] * 0.9, f"{T['median']} {med:.0f}°", fontsize=7)
        ax.set_xlim(0, 90)
        ax.set_xlabel(T["theta"])
        ax.set_ylabel(T["share"])
        save(fig, "zenith")

        # tilt prediction
        tl = res["tilt"]
        ang = [int(a) for a in tl]
        fig, ax = new(h=2.0)
        ax.plot(ang, [tl[str(a)] for a in ang], color=BLUE, lw=1.2, marker="o", ms=3.5)
        ax.set_xticks(ang)
        ax.set_ylim(0, 1.1)
        ax.set_xlabel(T["tilt"])
        ax.set_ylabel(T["ratio"])
        save(fig, "tilt")

        # hourly, full width
        fig, ax = new(w=7.0, h=2.0)
        allc = np.concatenate([raw[r_]["hourly"] for r_ in runs])
        mu = allc.mean()
        ax.axhspan(stats.poisson.ppf(0.025, mu), stats.poisson.ppf(0.975, mu), color=BAND, lw=0)
        ax.axhline(mu, color=INK2, lw=0.6)
        for run, col, lab in (("before", ORANGE, T["runA"]), ("main", BLUE, T["runB"])):
            if len(raw[run]["hours"]):
                ax.plot(raw[run]["hours"], raw[run]["hourly"], color=col, lw=1, marker="o", ms=2, label=lab)
        ax.xaxis.set_major_formatter(mdates.DateFormatter(T["date"]))
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12]))
        ax.set_ylabel(T["hour_y"])
        ax.legend(frameon=False, ncol=2, loc="lower left")
        save(fig, "hourly")

        # detector, full width
        ph, dh, pm, dm = rays
        fig, axs = plt.subplots(1, 2, figsize=(7.0, 2.4), gridspec_kw=dict(width_ratios=[1, 1.9], wspace=0.05))
        fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.1)
        r = d_cm / 2
        for ax, (i, lab) in zip(axs, ((1, T["section"]), (0, T["side"]))):
            ax.set_aspect("equal")
            ax.axis("off")
            ax.text(0.5, -0.03, lab, transform=ax.transAxes, ha="center", va="top", fontsize=8)
            for P, D, col, lw, al in ((pm[:25], dm[:25], "#999999", 0.5, 0.6), (ph, dh, BLUE, 0.6, 0.85)):
                for p0, d0 in zip(P, D):
                    tt = np.array([-3.2, 3.2]) / max(abs(d0[2]), 0.05)
                    ax.plot(p0[i] + tt * d0[i], p0[2] + tt * d0[2], color=col, lw=lw, alpha=al)
            for s in (1, -1):
                zc = s * sep_cm / 2
                if i == 1:
                    ax.add_patch(Circle((0, zc), r, facecolor=BAND, edgecolor=INK, lw=0.9, zorder=5))
                else:
                    ax.add_patch(Rectangle((-glass_cm / 2, zc - r), glass_cm, 2 * r, facecolor=BAND, edgecolor=INK, lw=0.9, zorder=5))
            w = 2.2 if i == 1 else glass_cm / 2 + 1.2
            ax.set_xlim(-w, w)
            ax.set_ylim(-2.2, 2.2)
        fig.savefig(os.path.join(od, "detector.pdf"))
        plt.close(fig)
