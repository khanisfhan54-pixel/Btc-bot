from typing import Iterator, Tuple

def walk_forward_splits(n_samples: int, train_size: int, test_size: int, step: int) -> Iterator[Tuple[range, range]]:
    i = train_size
    while i + test_size <= n_samples:
        yield range(0, i), range(i, i + test_size)
        i += step
