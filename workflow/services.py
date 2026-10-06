"""PRD 30 - Approval request: pilih step berdasarkan amount, approve/reject."""
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import ApprovalRequest, ApprovalWorkflow, WorkflowStatus


def build_request(company, model_name, instance, amount, requester, workflow=None, comments=''):
    """Buat approval request dengan step pertama yang cocok dengan amount."""
    if workflow is None:
        workflow = ApprovalWorkflow.objects.filter(
            company=company, model_name=model_name, is_active=True
        ).first()
    if workflow is None:
        raise ValidationError(
            f'Tidak ada approval workflow aktif untuk {model_name} di {company.company_code}.'
        )

    step = next((s for s in workflow.steps.all() if s.matches(amount)), None)
    if step is None:
        raise ValidationError(
            f'Tidak ada approval step yang cocok untuk nominal {amount}. '
            'Periksa min_amount / max_amount pada workflow.'
        )

    return ApprovalRequest.objects.create(
        workflow=workflow, step=step, requester=requester,
        model_name=model_name, object_id=str(instance.pk),
        object_repr=str(instance)[:255], amount=amount, comments=comments,
    )


def approve(request, approver):
    if request.status != WorkflowStatus.PENDING:
        raise ValidationError(f'Request sudah {request.get_status_display()}.')
    if request.approver_id and request.approver_id != approver.id:
        raise ValidationError('Request ini dialokasikan ke user lain.')

    request.status = WorkflowStatus.APPROVED
    request.approver = approver
    request.action_at = timezone.now()
    request.save(update_fields=['status', 'approver', 'action_at', 'updated_at'])
    return request


def reject(request, approver, reason=''):
    if request.status != WorkflowStatus.PENDING:
        raise ValidationError(f'Request sudah {request.get_status_display()}.')
    if not request.step.can_reject:
        raise ValidationError('Step ini tidak punya wewenang menolak.')

    request.status = WorkflowStatus.REJECTED
    request.approver = approver
    request.action_at = timezone.now()
    if reason:
        request.comments = f'{request.comments}\n[Rejected] {reason}'.strip()
    request.save(update_fields=['status', 'approver', 'action_at', 'comments', 'updated_at'])
    return request


def is_approved(model_name, object_id):
    return ApprovalRequest.objects.filter(
        model_name=model_name, object_id=str(object_id), status=WorkflowStatus.APPROVED
    ).exists()