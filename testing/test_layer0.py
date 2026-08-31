import unittest
import requests
import time

class TestLayer0(unittest.TestCase):
    BASE_URL = "http://localhost:8000"

    def test_health_check(self):
        """Test API Gateway health endpoint."""
        try:
            response = requests.get(f"{self.BASE_URL}/health", timeout=5)
            self.assertEqual(response.status_code, 200, "Health check should return 200")
            self.assertIn("status", response.json())
        except requests.exceptions.ConnectionError:
            self.fail("Could not connect to the API Gateway. Are the Docker containers running?")

    def test_checkout_endpoint(self):
        """Test the checkout endpoint against its healthy baseline (ERROR_RATE=0, SLOW_RATE=0)."""
        successes = 0
        failures = 0
        for _ in range(5):
            response = requests.get(f"{self.BASE_URL}/checkout/order123", timeout=10)
            if response.status_code == 200:
                successes += 1
            else:
                failures += 1
            time.sleep(0.5)

        # Baseline is healthy — real failures should come from injected code/traffic
        # changes, not defaults, so this expects success on every call.
        self.assertEqual(failures, 0, "Checkout endpoint failed against its healthy baseline")

    def test_inventory_endpoint(self):
        """Test the inventory endpoint."""
        successes = 0
        for _ in range(3):
            response = requests.get(f"{self.BASE_URL}/inventory", timeout=10)
            if response.status_code == 200:
                successes += 1
        self.assertTrue(successes > 0, "Inventory endpoint not returning 200 OK")

if __name__ == "__main__":
    print("Testing Layer 0: System Under Observation...")
    print("Make sure the Target_Client K8s deployments are running first!\n")
    unittest.main()
