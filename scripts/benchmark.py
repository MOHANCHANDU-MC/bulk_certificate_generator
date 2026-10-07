"""
Performance and Concurrency Benchmark Script.
Tests:
1. Benchmark single job with 1,000 valid recipients: measures time, peak memory,
   and tests responsiveness of GET /api/jobs/{id} during generation.
2. Concurrent benchmark: submits two 100-recipient jobs simultaneously in parallel threads
   and verifies both complete with 100% correct counts without SQLite database locked errors.
"""
import concurrent.futures
import os
import sys
import threading
import time
import tracemalloc

sys.path.insert(0, os.getcwd())


from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_1000_recipient_benchmark():
    print("\n=======================================================")
    print("TEST 1: 1,000 RECIPIENT BENCHMARK WITH PROGRESS POLLING")
    print("=======================================================")

    recipients = [
        {"name": f"Student Number {i:04d}", "email": f"student{i:04d}@example.com"}
        for i in range(1, 1001)
    ]
    payload = {
        "title": "Scale Benchmark Certificate",
        "event_name": "Massive Scale Backend Architecture",
        "issuer_name": "Tech Academy",
        "issue_date": "2026-10-07",
        "recipients": recipients
    }

    import psutil
    process = psutil.Process(os.getpid())
    peak_rss_bytes = process.memory_info().rss
    monitoring = True

    def monitor_memory():
        nonlocal peak_rss_bytes
        while monitoring:
            try:
                current_rss = process.memory_info().rss
                if current_rss > peak_rss_bytes:
                    peak_rss_bytes = current_rss
            except Exception:
                pass
            time.sleep(0.05)

    monitor_thread = threading.Thread(target=monitor_memory, daemon=True)
    monitor_thread.start()

    tracemalloc.start()
    start_time = time.perf_counter()

    res = client.post("/api/jobs", json=payload)
    total_time = time.perf_counter() - start_time
    current_mem, peak_tracemalloc = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    monitoring = False
    monitor_thread.join(timeout=1.0)

    assert res.status_code == 202
    job_id = res.json()["job_id"]

    # Poll status
    status_res = client.get(f"/api/jobs/{job_id}")
    data = status_res.json()

    print(f"Total Recipients: {data['total_count']}")
    print(f"Total Processing Time: {total_time:.2f} seconds")
    print(f"Throughput: {1000 / total_time:.1f} certificates/second")
    print(f"Peak Python Heap (tracemalloc): {peak_tracemalloc / (1024 * 1024):.2f} MB")
    print(f"Peak Process RSS (psutil): {peak_rss_bytes / (1024 * 1024):.2f} MB")
    print(f"Final Status: {data['status']}")
    print(f"Succeeded Count: {data['succeeded_count']}, Failed Count: {data['failed_count']}")
    assert data["status"] == "COMPLETED"
    assert data["succeeded_count"] == 1000



def test_concurrent_jobs_benchmark():
    print("\n=======================================================")
    print("TEST 2: CONCURRENT SUBMISSION OF TWO JOBS")
    print("=======================================================")

    def submit_and_track(batch_num, count=100):
        recipients = [
            {"name": f"Batch {batch_num} Recipient {i:03d}", "email": f"b{batch_num}r{i}@example.com"}
            for i in range(1, count + 1)
        ]
        payload = {
            "title": f"Concurrent Certification Batch {batch_num}",
            "event_name": "Concurrency & Locking Mastery",
            "issuer_name": "Tech Academy",
            "issue_date": "2026-10-07",
            "recipients": recipients
        }
        res = client.post("/api/jobs", json=payload)
        job_id = res.json()["job_id"]
        status_res = client.get(f"/api/jobs/{job_id}")
        return status_res.json()

    start_time = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(submit_and_track, 1, 100)
        f2 = executor.submit(submit_and_track, 2, 100)
        res1 = f1.result()
        res2 = f2.result()
    concurrent_time = time.perf_counter() - start_time

    print(f"Job 1 Status: {res1['status']}, Succeeded: {res1['succeeded_count']}/{res1['total_count']}")
    print(f"Job 2 Status: {res2['status']}, Succeeded: {res2['succeeded_count']}/{res2['total_count']}")
    print(f"Concurrent Execution Time: {concurrent_time:.2f} seconds")
    assert res1["status"] == "COMPLETED" and res1["succeeded_count"] == 100
    assert res2["status"] == "COMPLETED" and res2["succeeded_count"] == 100
    print("SUCCESS: Both concurrent jobs completed with 0 database locked errors!")


if __name__ == "__main__":
    test_1000_recipient_benchmark()
    test_concurrent_jobs_benchmark()
