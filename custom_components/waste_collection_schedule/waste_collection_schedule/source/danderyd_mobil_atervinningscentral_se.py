"""Danderyd's mobile recycling-centre schedule."""

from typing import ClassVar, final

from waste_collection_schedule import waste_types as wt
from waste_collection_schedule.base_source import BaseSource
from waste_collection_schedule.service.DanderydMobile import (
    DanderydMobileParser,
    DanderydMobileRetriever,
)
from waste_collection_schedule.transformers import ICSTransformer

_URL = (
    "https://www.danderyd.se/bygga-bo-och-miljo/avfall-atervinning-och-aterbruk/"
    "mobil-atervinningscentral/"
)
_EVENT_TITLE = "Mobil återvinningscentral"


@final
class Source(BaseSource):
    TITLE = "Danderyds mobila återvinningscentral"
    DESCRIPTION = "Schedule for Danderyd's mobile recycling centre."
    URL = _URL
    COUNTRY = "se"
    WASTE_TYPES: ClassVar[list] = [wt.RECYCLABLES]

    TEST_CASES: ClassVar[dict] = {"Danderyd": {}}

    retrieve = DanderydMobileRetriever()
    parse = DanderydMobileParser()
    transform = ICSTransformer(
        type_value_map={_EVENT_TITLE: wt.RECYCLABLES},
        carry_raw_label=False,
    )
