import sys
import engine

def test():
    # normal
    print(engine._best_bid_ask({'bids': [[84000, 1], [84100, 1]], 'asks': [[84300, 1], [84200, 1]]}))
    # None
    print(engine._best_bid_ask({'bids': [None], 'asks': []}))

if __name__ == "__main__":
    try:
        test()
    except Exception as e:
        print(f"Error: {e}")
