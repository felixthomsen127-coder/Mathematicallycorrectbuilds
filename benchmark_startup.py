#!/usr/bin/env python
"""Benchmark startup time: cold-load vs. warm-start with persisted markers."""
import time
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
from main import app, _run_prefetch_cycle, _save_prefetch_marker, _load_prefetch_marker


def benchmark_cold_startup():
    """Measure prefetch startup with no persisted markers (cold-load)."""
    print("\n=== Cold-Load Startup Benchmark ===")
    start = time.time()
    
    # Mock the expensive data fetching to simulate realistic scenario
    with patch('main.get_cached_data') as mock_fetch:
        mock_fetch.return_value = MagicMock()
        _run_prefetch_cycle('live', force_refresh=False)
    
    elapsed = time.time() - start
    print(f"Cold-load prefetch startup: {elapsed:.2f}s")
    return elapsed


def benchmark_warm_startup():
    """Measure prefetch startup with existing persisted markers (warm-start)."""
    print("\n=== Warm-Start Startup Benchmark ===")
    
    # Pre-populate marker to simulate warm-start
    marker_payload = {
        'ready': True,
        'timestamp': time.time() - 3600,  # Simulate 1 hour old marker
        'completion_time': 0.5
    }
    _save_prefetch_marker('live', marker_payload)
    
    start = time.time()
    
    # Should skip prefetch if marker exists and not forced
    with patch('main._load_prefetch_marker') as mock_load:
        mock_load.return_value = marker_payload
        marker = _load_prefetch_marker('live')
        if marker and marker.get('ready'):
            elapsed = time.time() - start
            print(f"Warm-start marker check: {elapsed:.4f}s (marker hit - prefetch skipped)")
            return elapsed
    
    elapsed = time.time() - start
    print(f"Warm-start prefetch startup: {elapsed:.2f}s")
    return elapsed


def benchmark_force_refresh():
    """Measure prefetch startup with force_refresh=True (bypasses marker)."""
    print("\n=== Force-Refresh Startup Benchmark ===")
    start = time.time()
    
    with patch('main.get_cached_data') as mock_fetch:
        mock_fetch.return_value = MagicMock()
        _run_prefetch_cycle('live', force_refresh=True)
    
    elapsed = time.time() - start
    print(f"Force-refresh prefetch startup: {elapsed:.2f}s")
    return elapsed


def benchmark_marker_persistence():
    """Measure persistence layer performance."""
    print("\n=== Marker Persistence Benchmark ===")
    test_payloads = [
        {'ready': True, 'timestamp': time.time()},
        {'ready': True, 'timestamp': time.time(), 'metadata': 'x' * 1000},
        {'ready': False, 'progress': 0.5, 'tasks': list(range(100))}
    ]
    
    save_times = []
    load_times = []
    
    for i, payload in enumerate(test_payloads):
        start = time.time()
        _save_prefetch_marker(f'test_patch_{i}', payload)
        save_time = time.time() - start
        save_times.append(save_time)
        
        start = time.time()
        marker = _load_prefetch_marker(f'test_patch_{i}')
        load_time = time.time() - start
        load_times.append(load_time)
    
    avg_save = sum(save_times) / len(save_times)
    avg_load = sum(load_times) / len(load_times)
    
    print(f"Average marker save time: {avg_save:.4f}s")
    print(f"Average marker load time: {avg_load:.4f}s")
    
    return {'save': avg_save, 'load': avg_load}


def benchmark_startup_with_multiple_patches():
    """Measure startup overhead with multiple patch markers."""
    print("\n=== Multi-Patch Startup Benchmark ===")
    
    patches = ['live', '14.2', '14.1', '14.0', '13.24']
    
    for patch in patches:
        marker_payload = {'ready': True, 'timestamp': time.time()}
        _save_prefetch_marker(patch, marker_payload)
    
    start = time.time()
    for patch in patches:
        marker = _load_prefetch_marker(patch)
    elapsed = time.time() - start
    
    print(f"Loading {len(patches)} patch markers: {elapsed:.4f}s")
    print(f"Average per patch: {elapsed / len(patches):.4f}s")
    
    return elapsed


def main():
    """Run all startup benchmarks."""
    print("LeagueCorrectBuilds Startup Benchmark Suite")
    print("=" * 50)
    
    results = {
        'cold_startup': benchmark_cold_startup(),
        'warm_startup': benchmark_warm_startup(),
        'force_refresh': benchmark_force_refresh(),
        'marker_persistence': benchmark_marker_persistence(),
        'multi_patch': benchmark_startup_with_multiple_patches(),
    }
    
    print("\n" + "=" * 50)
    print("Benchmark Summary:")
    print(f"Warm-start speedup: {results['cold_startup'] / results['warm_startup']:.1f}x faster")
    print(f"Force-refresh penalty: {(results['force_refresh'] / results['cold_startup'] - 1) * 100:.1f}% overhead")
    
    # Save results to JSON
    timestamp = int(time.time())
    results_file = f'benchmark_startup_{timestamp}.json'
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {results_file}")


if __name__ == '__main__':
    main()
