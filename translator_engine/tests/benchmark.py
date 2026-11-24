import time
import statistics
import importlib.util

spec = importlib.util.spec_from_file_location(
    "quality",
    "tests/test_quality.py"
)

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

samples = [
    "Hello, how are you?",
    "I will protect you.",
    "What are you doing here?",
    "I never wanted to lose you!",
    "Even if the whole world stands against me, I will never give up."
]

times = []

print("\n=== TRANSLATION BENCHMARK ===")

for text in samples:
    start = time.perf_counter()
    result = module.translate(text)
    elapsed = time.perf_counter() - start

    times.append(elapsed)

    print(f"\nEN: {text}")
    print(f"AR: {result}")
    print(f"Time: {elapsed:.3f} seconds")

print("\n=== SUMMARY ===")
print(f"Average: {statistics.mean(times):.3f} seconds")
print(f"Fastest: {min(times):.3f} seconds")
print(f"Slowest: {max(times):.3f} seconds")
