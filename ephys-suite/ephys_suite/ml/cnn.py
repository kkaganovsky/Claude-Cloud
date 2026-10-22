"""1-D CNN (raw event window + engineered-feature head), inference side. Training lives with the weights, outside this package."""
import numpy as np, torch, torch.nn as nn, torch.nn.functional as Fn

DEV = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")


WPRE, WPOST = 300, 1200      # layout of the stored wide windows (mlcore.window_wide)


def _two(X, Fe, nb):
    b = X[:, :nb].mean(1, keepdims=True)
    sd = np.maximum(10 ** Fe[:, 16:17] - 1e-6, 1e-3)
    amp = np.maximum(Fe[:, 0:1], 1.0)
    return [np.clip((X - b) / sd, -60, 60) / 10.0, np.clip((X - b) / amp, -3, 3)]


def prep(X, Fe, pre=100, post=400, ms=False, ms_factor=3):
    """X: (n,1500) wide windows (peak at 300 = 30 ms before); crop to [pre, post] samples around the peak (10 kHz).
    ms=True appends a coarse channel pair: the whole wide window average-pooled by ms_factor (long context)."""
    X = X.astype(np.float32)
    Xc = X[:, WPRE - pre:WPRE + post]
    ch = _two(Xc, Fe, min(60, max(10, int(pre * 0.6))))
    if ms:
        n = (X.shape[1] // ms_factor) * ms_factor
        Xd = X[:, :n].reshape(len(X), -1, ms_factor).mean(2)
        ch += _two(Xd, Fe, 60 // ms_factor)
        L = min(c.shape[1] for c in ch)
        ch = [c[:, :L] for c in ch]
        if any(c.shape[1] != Xc.shape[1] for c in ch):          # fine branch keeps its own length: pad coarse/fine to common L
            L = max(c.shape[1] for c in ch)
            ch = [np.pad(c, ((0, 0), (0, L - c.shape[1]))) for c in ch]
    return np.stack(ch, 1).astype(np.float32)


class Branch(nn.Module):
    def __init__(self, cin, w):
        super().__init__()
        self.c1 = nn.Sequential(nn.Conv1d(cin, w, 7, padding=3), nn.BatchNorm1d(w), nn.ReLU(), nn.Conv1d(w, w, 7, padding=3), nn.BatchNorm1d(w), nn.ReLU(), nn.MaxPool1d(2))
        self.c2 = nn.Sequential(nn.Conv1d(w, 2 * w, 5, padding=4, dilation=2), nn.BatchNorm1d(2 * w), nn.ReLU(), nn.Conv1d(2 * w, 2 * w, 5, padding=2), nn.BatchNorm1d(2 * w), nn.ReLU(), nn.MaxPool1d(2))
        self.c3 = nn.Sequential(nn.Conv1d(2 * w, 4 * w, 3, padding=2, dilation=2), nn.BatchNorm1d(4 * w), nn.ReLU())

    def forward(self, x):
        z = self.c3(self.c2(self.c1(x)))
        return torch.cat([z.mean(2), z.amax(2)], 1)


class Net(nn.Module):
    """Single-scale (2 input channels) or multiscale (4 channels: fine pair + coarse pair, one branch each)."""
    def __init__(self, nf, width=32, drop=0.2, cin=2):
        super().__init__()
        self.nb = cin // 2
        self.br = nn.ModuleList([Branch(2, width) for _ in range(self.nb)])
        self.f = nn.Sequential(nn.Linear(nf, 32), nn.ReLU())
        self.h = nn.Sequential(nn.Dropout(drop), nn.Linear(8 * width * self.nb + 32, 64), nn.ReLU(), nn.Dropout(drop), nn.Linear(64, 1))

    def forward(self, x, f):
        z = torch.cat([b(x[:, 2 * i:2 * i + 2]) for i, b in enumerate(self.br)] + [self.f(f)], 1)
        return self.h(z).squeeze(1)


@torch.no_grad()
def predict(net, Xp, Fs, idx, bs=4096):
    net.eval(); out = []
    for i in range(0, len(idx), bs):
        b = idx[i:i + bs]
        out.append(torch.sigmoid(net(torch.from_numpy(Xp[b]).to(DEV), torch.from_numpy(Fs[b]).to(DEV))).cpu().numpy())
    return np.concatenate(out)
