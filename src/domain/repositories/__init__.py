"""Domain repository protocols, one module per aggregate.

Import each protocol from its own module (e.g. ``from src.domain.repositories.track import TrackRepositoryProtocol``,
``from src.domain.repositories.uow import UnitOfWorkProtocol``). There is no
re-export layer — one import path per protocol.
"""
