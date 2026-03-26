#!/usr/bin/env python
"""Benchmark optimizer throughput with ProcessPoolExecutor vs. in-process."""
import time
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from unittest.mock import patch, MagicMock
from main import _balanced_worker_count, app


def mock_optimization_job(job_id: int):
    """Simulate an optimization job that takes ~100-200ms."""
    time.sleep(0.1 + (job_id % 10) * 0.01)
    return {'job_id': job_id, 'score': 100 + job_id}


def benchmark_sequential():
    """Baseline: sequential optimization jobs."""
    print("\n=== Sequential (in-process) Benchmark ===")
    job_count = 20
    
    start = time.time()
    results = []
    for i in range(job_count):
        result = mock_optimization_job(i)
        results.append(result)
    elapsed = time.time() - start
    
    throughput = job_count / elapsed
    print(f"Jobs completed: {job_count}")
    print(f"Time elapsed: {elapsed:.2f}s")
    print(f"Throughput: {throughput:.2f} jobs/sec")
    
    return elapsed, throughput


def benchmark_multiprocess(workers: int = None):
    """Measure multiprocess optimization with ProcessPoolExecutor."""
    if workers is None:
        workers = _balanced_worker_count()
    
    print(f"\n=== ProcessPoolExecutor Benchmark ({workers} workers) ===")
    job_count = 20
    
    start = time.time()
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(mock_optimization_job, i) for i in range(job_count)]
        results = [f.result() for f in as_completed(futures)]
    elapsed = time.time() - start
    
    throughput = job_count / elapsed
    print(f"Jobs completed: {job_count}")
    print(f"Workers used: {workers}")
    print(f"Time elapsed: {elapsed:.2f}s")
    print(f"Throughput: {throughput:.2f} jobs/sec")
    
    return elapsed, throughput


def benchmark_worker_scaling():
    """Measure performance across different worker counts."""
    print("\n=== Worker Scaling Benchmark ===")
    job_count = 40
    
    results = {}
    for workers in [1, 2, 4, 6, 8]:
        print(f"\nTesting with {workers} worker(s)...")
        start = time.time()
        
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(mock_optimization_job, i) for i in range(job_count)]
            completed = 0
            for f in as_completed(futures):
                completed += 1
                if completed % 10 == 0:
                    print(f"  {completed}/{job_count} jobs completed...")
        
        elapsed = time.time() - start
        throughput = job_count / elapsed
        results[workers] = {
            'time': elapsed,
            'throughput': throughput
        }
        print(f"  {workers} workers: {elapsed:.2f}s ({throughput:.2f} jobs/sec)")
    
    # Find optimal worker count
    best_workers = max(results.keys(), key=lambda w: results[w]['throughput'])
    print(f"\nOptimal worker count: {best_workers} ({results[best_workers]['throughput']:.2f} jobs/sec)")
    
    return results


def benchmark_large_batch():
    """Measure throughput on larger batch."""
    print("\n=== Large Batch Benchmark ===")
    job_count = 100
    workers = _balanced_worker_count()
    
    print(f"Processing {job_count} jobs with {workers} workers...")
    start = time.time()
    
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(mock_optimization_job, i) for i in range(job_count)]
        completed = 0
        for f in as_completed(futures):
            completed += 1
            if completed % 25 == 0:
                elapsed_so_far = time.time() - start
                throughput_so_far = completed / elapsed_so_far
                print(f"  {completed}/{job_count} jobs | {throughput_so_far:.2f} jobs/sec")
    
    total_elapsed = time.time() - start
    final_throughput = job_count / total_elapsed
    
    print(f"\nTotal time: {total_elapsed:.2f}s")
    print(f"Final throughput: {final_throughput:.2f} jobs/sec")
    
    return total_elapsed, final_throughput


def main():
    """Run all optimizer throughput benchmarks."""
    print("LeagueCorrectBuilds Optimizer Throughput Benchmark Suite")
    print("=" * 60)
    
    # Baseline sequential
    seq_time, seq_throughput = benchmark_sequential()
    
    # Multiprocess with balanced workers
    mp_time, mp_throughput = benchmark_multiprocess()
    
    # Worker scaling analysis
    scaling_results = benchmark_worker_scaling()
    
    # Large batch
    large_time, large_throughput = benchmark_large_batch()
    
    print("\n" + "=" * 60)
    print("Benchmark Summary:")
    print(f"Sequential: {seq_throughput:.2f} jobs/sec")
    print(f"Multiprocess: {mp_throughput:.2f} jobs/sec")
    print(f"Speedup: {mp_throughput / seq_throughput:.1f}x faster")
    print(f"\nLarge batch (100 jobs): {large_throughput:.2f} jobs/sec")
    
    import json
    import time as time_module
    timestamp = int(time_module.time())
    results_file = f'benchmark_optimizer_{timestamp}.json'
    
    results = {
        'sequential': {'throughput': seq_throughput, 'time': seq_time},
        'multiprocess': {'throughput': mp_throughput, 'time': mp_time},
        'speedup': mp_throughput / seq_throughput,
        'worker_scaling': scaling_results,
        'large_batch': {'throughput': large_throughput, 'time': large_time}
    }
    
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {results_file}")


if __name__ == '__main__':
    main()
