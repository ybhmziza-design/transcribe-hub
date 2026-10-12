"""
Comprehensive tests for TranscribeHub:
- Admin-only task posting and review
- Public visitors can browse and view tasks freely
- Workers register and must pay KES 2,500 entry fee through Paystack
- Paystack initialization, callback verification, and task access gates
"""
import unittest, json, os
import database, exporter
from app import app

class TranscribeHubPaystackTests(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        conn = database.get_db_connection()
        conn.execute('PRAGMA foreign_keys = OFF')
        conn.execute('DELETE FROM task_activities')
        conn.execute('DELETE FROM transcriptions')
        conn.execute('DELETE FROM payments')
        conn.execute('DELETE FROM tasks')
        conn.execute('DELETE FROM users')
        conn.execute('PRAGMA foreign_keys = ON')
        conn.commit()
        conn.close()

    def test_db_clean_initial_state(self):
        conn = database.get_db_connection()
        tasks_count = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        conn.close()
        self.assertEqual(tasks_count, 0)

    def test_paystack_settings_default(self):
        amount, curr = database.get_entry_fee()
        self.assertEqual(amount, 573)
        self.assertEqual(curr, 'KES')

    def test_admin_updates_paystack_settings(self):
        with self.client.session_transaction() as sess:
            sess['is_admin'] = True

        r = self.client.post('/admin/settings', data={
            'paystack_public_key': 'pk_live_custom123',
            'paystack_secret_key': 'sk_live_custom456',
            'entry_fee_amount': '573',
            'entry_fee_currency': 'KES'
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)

        pub = database.get_setting('paystack_public_key')
        self.assertEqual(pub, 'pk_live_custom123')

    def test_worker_paystack_workflow(self):
        # 1. Admin creates a task
        with self.client.session_transaction() as sess:
            sess['is_admin'] = True

        self.client.post('/tasks/new', data={
            'title': 'Paystack Video Task',
            'description': 'Transcription task test',
            'category': 'Technology',
            'language': 'English',
            'difficulty': 'Medium',
            'reward': '35.00',
            'duration_est': '05:00',
            'guidelines': 'Clean verbatim',
            'video_choice': 'url',
            'video_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4'
        })

        conn = database.get_db_connection()
        task = conn.execute("SELECT id FROM tasks WHERE title='Paystack Video Task'").fetchone()
        conn.close()
        task_id = task['id']

        # 2. Worker signs up
        database.create_user('paystack_worker', 'worker@paystacktest.com', 'mypassword', 'Paystack Worker', '0712345678')
        user = database.authenticate_user('paystack_worker', 'mypassword')

        # 3. Worker logged in, unpaid
        with self.client.session_transaction() as sess:
            sess['is_admin'] = False
            sess['user_id'] = user['id']

        # 4. Attempt to claim without Paystack payment -> redirect to /pay-entry-fee
        r_claim_unpaid = self.client.post(f'/tasks/{task_id}/claim', follow_redirects=False)
        self.assertEqual(r_claim_unpaid.status_code, 302)
        self.assertIn('/pay-entry-fee', r_claim_unpaid.headers['Location'])

        # 5. Initialize Paystack payment endpoint
        r_init = self.client.post('/api/paystack/initialize')
        self.assertEqual(r_init.status_code, 200)
        init_data = json.loads(r_init.data)
        self.assertIn('reference', init_data)

        # 6. Verify Paystack payment via API (e.g. inline popup completion or callback)
        tx_ref = init_data['reference']
        r_verify = self.client.post('/api/paystack/verify', data=json.dumps({
            'reference': tx_ref
        }), content_type='application/json')
        self.assertEqual(r_verify.status_code, 200)
        verify_data = json.loads(r_verify.data)
        self.assertEqual(verify_data['status'], 'success')

        # Verify in DB
        u = database.get_user_by_id(user['id'])
        self.assertEqual(u['is_paid'], 1)

        # 7. Worker can now claim task
        r_claim_paid = self.client.post(f'/tasks/{task_id}/claim', follow_redirects=True)
        self.assertEqual(r_claim_paid.status_code, 200)
        self.assertIn(b'Transcription Studio', r_claim_paid.data)

        # 8. Studio access permitted
        r_studio = self.client.get(f'/tasks/{task_id}/studio')
        self.assertEqual(r_studio.status_code, 200)

if __name__ == '__main__':
    unittest.main()
