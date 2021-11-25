#  Copyright (c) 2021, Manfred Moitzi
#  License: MIT License

from typing import TYPE_CHECKING, List
import logging

from ezdxf.math import Vec2, UCS, NULLVEC, sign
from ezdxf.lldxf import const
from ezdxf.entities import DimStyleOverride, Dimension
from .dim_base import (
    BaseDimensionRenderer,
    get_required_defpoint,
    compile_mtext,
)

if TYPE_CHECKING:
    from ezdxf.eztypes import GenericLayoutType

__all__ = ["OrdinateDimension"]

logger = logging.getLogger("ezdxf")


class OrdinateDimension(BaseDimensionRenderer):
    # Required defpoints:
    # defpoint = origin (group code 10)
    # defpoint2 = feature location (group code 13)
    # defpoint3 = end of leader (group code 14)
    def __init__(
        self,
        dimension: Dimension,
        ucs: "UCS" = None,
        override: DimStyleOverride = None,
    ):
        self.origin: Vec2 = get_required_defpoint(dimension, "defpoint")
        self.feature_location: Vec2 = get_required_defpoint(
            dimension, "defpoint2"
        )
        self.end_of_leader: Vec2 = get_required_defpoint(dimension, "defpoint3")
        self.leader_offset = self.end_of_leader - self.feature_location
        # x_type = not(y_type)
        self.x_type = bool(  # x-type is set!
            dimension.dxf.get("dimtype", 0) & const.DIM_ORDINATE_TYPE
        )
        super().__init__(dimension, ucs, override)

        # Main direction vectors for x-type:
        self.direction: Vec2 = Vec2(
            -1.0 if self.leader_offset.x < 0.0 else 1.0, 0
        )
        self.dir_ortho: Vec2 = Vec2(
            0, -1.0 if self.leader_offset.y < 0.0 else 1.0
        )
        if not self.x_type:
            self.direction, self.dir_ortho = self.dir_ortho, self.direction

        # Class specific setup:
        self.update_measurement()
        if self.tol.has_limits:
            self.tol.update_limits(self.measurement.value)

        # Text width and -height is required first, text location and -rotation
        # are not valid yet:
        self.text_box = self.init_text_box()
        self.setup_text_location()

        # update text box location and -rotation:
        self.text_box.center = self.measurement.text_location
        self.text_box.angle = self.measurement.text_rotation
        self.geometry.set_text_box(self.text_box)

        # Update final text location in the DIMENSION entity:
        self.dimension.dxf.text_midpoint = self.measurement.text_location

    def setup_text_location(self) -> None:
        """Setup geometric text properties location and rotation."""
        offset = self.measurement.text_vertical_distance()
        offset_vec = (Vec2(-1, 0) if self.x_type else Vec2(0, 1)) * offset

        self.measurement.text_location = (
            self.end_of_leader
            + self.dir_ortho * (self.text_box.width * 0.5)
            + offset_vec
        )
        if self.measurement.text_rotation is None:
            # if no user text rotation is set:
            if self.x_type:
                self.measurement.text_rotation = 90.0
            else:
                self.measurement.text_rotation = 0.0

    def update_measurement(self) -> None:
        distance: Vec2 = self.feature_location - self.origin
        # ordinate measurement is always absolute:
        self.measurement.update(
            abs(distance.x) if self.x_type else abs(distance.y)
        )

    def get_defpoints(self) -> List[Vec2]:
        return [
            self.origin,
            self.feature_location,
            self.end_of_leader,
        ]

    def transform_ucs_to_wcs(self) -> None:
        """Transforms dimension definition points into WCS or if required into
        OCS.
        """

        def from_ucs(attr, func):
            point = dxf.get(attr, NULLVEC)
            dxf.set(attr, func(point))

        dxf = self.dimension.dxf
        ucs = self.geometry.ucs
        from_ucs("defpoint", ucs.to_wcs)
        from_ucs("defpoint2", ucs.to_wcs)
        from_ucs("defpoint3", ucs.to_wcs)
        from_ucs("text_midpoint", ucs.to_ocs)

    def render(self, block: "GenericLayoutType") -> None:
        """Main method to create dimension geometry of basic DXF entities in the
        associated BLOCK layout.

        Args:
            block: target BLOCK for rendering

        """
        super().render(block)
        self.add_extension_line()
        measurement = self.measurement
        if measurement.text:
            if self.geometry.supports_dxf_r2000:
                text = compile_mtext(measurement, self.tol)
            else:
                text = measurement.text
            self.add_measurement_text(
                text, measurement.text_location, measurement.text_rotation
            )
        self.geometry.add_defpoints(self.get_defpoints())

    def add_extension_line(self) -> None:
        attribs = self.extension_lines.dxfattribs(1)
        direction = self.dir_ortho  # leader direction or text direction
        leg_size = self.arrows.arrow_size * 2.0
        #            /---1---TEXT
        # x----0----/
        # d0 = distance from feature location (x) to 1st upward junction
        d0 = direction.project(self.leader_offset).magnitude - 2.0 * leg_size

        start0 = self.feature_location + direction * self.extension_lines.offset
        end0 = self.feature_location + direction * max(leg_size, d0)
        start1 = self.end_of_leader - direction * leg_size
        end1 = self.end_of_leader
        if self.measurement.vertical_placement != 0:
            end1 += direction * self.text_box.width

        self.add_line(start0, end0, dxfattribs=attribs)
        self.add_line(end0, start1, dxfattribs=attribs)
        self.add_line(start1, end1, dxfattribs=attribs)

    def add_measurement_text(
        self, dim_text: str, pos: Vec2, rotation: float
    ) -> None:
        """Add measurement text to dimension BLOCK.

        Args:
            dim_text: dimension text
            pos: text location
            rotation: text rotation in degrees

        """
        attribs = self.measurement.dxfattribs()
        self.add_text(dim_text, pos=pos, rotation=rotation, dxfattribs=attribs)