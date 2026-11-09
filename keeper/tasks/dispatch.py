from celery import shared_task

from keeper.services.dispatch import run_dispatch_wave


@shared_task(acks_late=True)
def dispatch_ride(ride_id, dispatch_round, wave):
    """
    Une vague de dispatch, puis reprogrammation de la suivante.
    (dispatch_round, wave) rend la tâche idempotente : une tâche obsolète
    (course acceptée, annulée, relancée…) ne fait rien.
    """
    countdown = run_dispatch_wave(ride_id, dispatch_round, wave)
    if countdown is not None:
        dispatch_ride.apply_async((ride_id, dispatch_round, wave + 1), countdown=countdown)
