"""Sample data alur persetujuan + pencocokan step berdasarkan nominal.

Sample ini penting karena `ApprovalStep.matches()` yang menentukan approver:
kalau min/max tumpang tindih atau bolong, request bisa salah eskalasi.
"""

from decimal import Decimal

from django.test import TestCase

from core.management.commands.seed_data import Command
from core.models import Company, Currency, Group, User
from .models import ApprovalRequest, ApprovalStep, ApprovalWorkflow, WorkflowStatus


class DemoApprovalSeedTests(TestCase):
    def setUp(self):
        group = Group.objects.create(name='Grup WF', code='GWF')
        self.company = Company.objects.create(
            company_code='A', legal_name='PT A', display_name='PT A', group=group)
        Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        for slug in ('accountant', 'finance', 'owner'):
            User.objects.create_user(f'demo_a_{slug}', password='pw', is_staff=True)
            User.objects.create_user(f'demo_b_{slug}', password='pw', is_staff=True)

    def test_workflow_dan_request_idempotent(self):
        command = Command()
        for _ in range(2):
            command._demo_approval_workflows(self.company)
            command._demo_approval_requests(self.company)

        self.assertEqual(ApprovalWorkflow.objects.filter(company=self.company).count(), 2)
        supplier = ApprovalWorkflow.objects.get(company=self.company, model_name='SupplierBill')
        self.assertEqual(supplier.steps.count(), 3)
        self.assertEqual(ApprovalRequest.objects.count(), 2, 'request tidak boleh dobel')

        # Step dipilih dari nominal: 30jt masuk tier 10-100jt (Company Finance).
        request = ApprovalRequest.objects.get(object_id='BILL-DEMO-001')
        self.assertEqual(request.status, WorkflowStatus.PENDING)
        self.assertEqual(request.step.required_role, User.Role.COMPANY_FINANCE)
        self.assertTrue(request.step.matches(Decimal('30_000_000')))
        self.assertEqual(request.requester.username, 'demo_a_accountant')
        self.assertEqual(request.approver.username, 'demo_a_finance')

        # Batas atas inklusif: tepat 10jt masih kena step 10 (lower tier),
        # karena build_request memilih step PERTAMA yang cocok. Ini disengaja
        # supaya nominal batas tidak butuh approver lebih tinggi.
        low = supplier.steps.get(step_order=10)
        self.assertTrue(low.matches(Decimal('9_999_999')))
        self.assertTrue(low.matches(Decimal('10_000_000')))
        self.assertFalse(low.matches(Decimal('10_000_000.01')))

    def test_request_tidak_dibuat_untuk_company_tanpa_user_demo(self):
        other = Company.objects.create(
            company_code='Z', legal_name='PT Z', display_name='PT Z',
            group=Group.objects.get(code='GWF'))
        command = Command()
        command._demo_approval_workflows(other)
        command._demo_approval_requests(other)
        self.assertEqual(ApprovalRequest.objects.filter(workflow__company=other).count(), 0)
