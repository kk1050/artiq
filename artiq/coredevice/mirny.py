"""RTIO driver for Mirny (4 channel GHz PLLs)
"""

from artiq.language.core import nac3, Kernel, KernelInvariant, kernel
from artiq.language.units import us

from numpy import int32

from artiq.coredevice.core import Core
from artiq.coredevice.spi2 import *


SPI_CONFIG = (
    0 * SPI_OFFLINE
    | 0 * SPI_END
    | 0 * SPI_INPUT
    | 1 * SPI_CS_POLARITY
    | 0 * SPI_CLK_POLARITY
    | 0 * SPI_CLK_PHASE
    | 0 * SPI_LSB_FIRST
    | 0 * SPI_HALF_DUPLEX
)

# SPI clock write and read dividers
SPIT_WR = 4
SPIT_RD = 16

SPI_CS = 1

WE = 1 << 24

# supported CPLD code version
PROTO_REV_MATCH = 0x0


@nac3
class Mirny:
    """
    Mirny PLL-based RF generator.

    :param spi_device: SPI bus device
    :param refclk: Reference clock (SMA, MMCX or on-board 100 MHz oscillator)
        frequency in Hz
    :param clk_sel: Reference clock selection.
        Valid options are: "XO" - onboard crystal oscillator;
        "SMA" - front-panel SMA connector; "MMCX" - internal MMCX connector.
        Passing an integer writes it as ``clk_sel`` in the CPLD's register 1.
        The effect depends on the hardware revision.
    :param core_device: Core device name (default: "core")
    """
    core: KernelInvariant[Core]
    bus: KernelInvariant[SPIMaster]
    refclk: KernelInvariant[float]
    clk_sel_hw_rev: Kernel[list[int32]]
    hw_rev: Kernel[int32]
    clk_sel: Kernel[int32]

    def __init__(self, dmgr, spi_device, refclk=100e6, clk_sel="XO", core_device="core"):
        self.core = dmgr.get(core_device)
        self.bus = dmgr.get(spi_device)

        # reference clock frequency
        self.refclk = refclk
        if not (10 <= self.refclk / 1e6 <= 600):
            raise ValueError("Invalid refclk")

        # reference clock selection
        try:
            self.clk_sel_hw_rev = {
            # clk source: [reserved, reserved, v1.1, v1.0]
                "xo": [-1, -1, 0, 0],
                "mmcx": [-1, -1, 3, 2],
                "sma": [-1, -1, 2, 3],
            }[clk_sel.lower()]
        except AttributeError:  # not a string, fallback to int
            if clk_sel & 0x3 != clk_sel:
                raise ValueError("Invalid clk_sel") from None
            self.clk_sel_hw_rev = [clk_sel] * 4
        except KeyError:
            raise ValueError("Invalid clk_sel") from None

        self.clk_sel = -1

        # board hardware revision
        self.hw_rev = 0  # v1.0: 3, v1.1: 2

        # TODO: support clk_div on v1.0 boards

    @kernel
    def read_reg(self, addr: int32) -> int32:
        """Read a register"""
        self.bus.set_config_mu(
            SPI_CONFIG | SPI_INPUT | SPI_END, 24, SPIT_RD, SPI_CS
        )
        self.bus.write((addr << 25))
        return self.bus.read() & 0xFFFF

    @kernel
    def write_reg(self, addr: int32, data: int32):
        """Write a register"""
        self.bus.set_config_mu(SPI_CONFIG | SPI_END, 24, SPIT_WR, SPI_CS)
        self.bus.write((addr << 25) | WE | ((data & 0xFFFF) << 8))

    @kernel
    def init(self, blind: bool = False):
        """
        Initialize and detect Mirny.

        Select the clock source based the board's hardware revision.
        Raise ValueError if the board's hardware revision is not supported.

        :param blind: Verify presence and protocol compatibility. Raise ValueError on failure.
        """
        reg0 = self.read_reg(0)
        self.hw_rev = reg0 & 0x3

        if not blind:
            if (reg0 >> 2) & 0x3 != PROTO_REV_MATCH:
                # NAC3TODO raise ValueError("Mirny PROTO_REV mismatch")
                pass
            self.core.delay(100. * us)  # slack

        # select clock source
        self.clk_sel = self.clk_sel_hw_rev[self.hw_rev]

        if self.clk_sel < 0:
            # NAC3TODO raise ValueError("Hardware revision not supported")
            pass

        self.write_reg(1, (self.clk_sel << 4))
        self.core.delay(1000. * us)

    @kernel
    def set_att_mu(self, channel: int32, att: int32):
        """Set digital step attenuator in machine units.

        :param att: Attenuation setting, 8 bit digital.
        """
        self.bus.set_config_mu(SPI_CONFIG | SPI_END, 16, SPIT_WR, SPI_CS)
        self.bus.write(((channel | 8) << 25) | (att << 16))

    @kernel
    def write_ext(self, addr: int32, length: int32, data: int32):
        """Perform SPI write to a prefixed address"""
        self.bus.set_config_mu(SPI_CONFIG, 8, SPIT_WR, SPI_CS)
        self.bus.write(addr << 25)
        self.bus.set_config_mu(SPI_CONFIG | SPI_END, length, SPIT_WR, SPI_CS)
        if length < 32:
            data <<= 32 - length
        self.bus.write(data)
