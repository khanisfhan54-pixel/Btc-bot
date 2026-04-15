import numpy as np
from collections import Counter
from advanced_regime_engine import compute_hmm_regime

def generate_samples(n=10000):
    samples = []
    for _ in range(n):
        alpha = np.random.dirichlet([1,1,1])  # random probabilities
        out = compute_hmm_regime(alpha)
        samples.append(out["regime"])
    return samples

regimes = generate_samples()

counts = Counter(regimes)
total = sum(counts.values())

print("\n=== REGIME DISTRIBUTION ===")
for k, v in counts.items():
    print(f"{k}: {v/total:.2%}")
