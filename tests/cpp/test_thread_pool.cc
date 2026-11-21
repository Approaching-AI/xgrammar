#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <stdexcept>

#include "support/thread_pool.h"
using namespace xgrammar;

TEST(XGramamrThreadPoolTest, FunctionalTest) {
  ThreadPool pool(4);

  // Example 1: Use Submit to submit tasks with return values
  std::vector<std::shared_future<int>> futures;
  for (int i = 0; i < 8; ++i) {
    auto fut = pool.Submit([i] {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      std::cout << "Task " << i << " is running in thread " << std::this_thread::get_id() << "\n";
      return i * i;
    });
    futures.push_back(fut);
  }

  for (auto& fut : futures) {
    int result = fut.get();
    std::cout << "Result: " << result << "\n";
  }

  // Example 2: Use Execute to submit tasks without return values
  for (int i = 0; i < 5; ++i) {
    pool.Execute([i] {
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
      std::cout << "Execute task " << i << " is running in thread " << std::this_thread::get_id()
                << "\n";
    });
  }

  // Wait for task to complete
  pool.Join();
}

TEST(XGrammarThreadPoolTest, JoinRethrowsAfterOtherTasksFinish) {
  std::atomic<bool> finished{false};
  ThreadPool pool(2);
  pool.Execute([] { throw std::runtime_error("compile task failed"); });
  pool.Execute([&] { finished = true; });
  EXPECT_THROW(pool.Join(), std::runtime_error);
  EXPECT_TRUE(finished);
}

TEST(XGrammarThreadPoolTest, WaitRethrowsAndPoolCanDoMoreWork) {
  ThreadPool pool(2);
  pool.Execute([] { throw std::runtime_error("compile task failed"); });
  EXPECT_THROW(pool.Wait(), std::runtime_error);
  EXPECT_EQ(pool.Submit([] { return 42; }).get(), 42);
  EXPECT_NO_THROW(pool.Join());
}

TEST(XGrammarThreadPoolTest, UnwindingDrainsTasksWithoutReplacingCallerException) {
  std::atomic<bool> finished{false};
  EXPECT_THROW(
      {
        ThreadPool pool(2);
        pool.Execute([] { throw std::runtime_error("compile task failed"); });
        pool.Execute([&] { finished = true; });
        throw std::logic_error("submission failed");
      },
      std::logic_error
  );
  EXPECT_TRUE(finished);
}

// TEST(XGramamrThreadPoolTest, PressureTest) {
//   const size_t num_threads = std::thread::hardware_concurrency();
//   ThreadPool pool(num_threads);

//   const size_t num_tasks = 1000;
//   int counter = 0;
//   std::mutex counter_mutex;

//   auto start_time = std::chrono::high_resolution_clock::now();

//   for (size_t i = 0; i < num_tasks; ++i) {
//     pool.Execute([&counter, &counter_mutex, i]() {
//       std::this_thread::sleep_for(std::chrono::milliseconds(i % 50));
//       std::lock_guard<std::mutex> lock(counter_mutex);
//       counter++;
//     });
//   }

//   pool.Wait();

//   auto end_time = std::chrono::high_resolution_clock::now();

//   EXPECT_EQ(counter, static_cast<int>(num_tasks));

//   auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
//   std::cout << "Pressure test completed, time taken: " << duration.count() << " milliseconds.\n";
// }
