#!/usr/bin/env python3
import collections
import optparse
import os
import platform
from string import punctuation
import sys

import chpl_comm, chpl_compiler, chpl_platform, overrides
from compiler_utils import CompVersion, target_compiler_is_prgenv, get_compiler_version
from utils import memoize, run_command, warning

# This map has a key as a synonym and the value as the LLVM arch/cpu
# It is intended to map from PrgEnv target cpus (e.g. craype-sandybridge)
# when these names differ from the LLVM ones.
cpu_llvm_synonyms = {
  'knc':           'none',
  'mic-knl':       'knl',
  'x86-skylake':   'skylake-avx512',
  'x86-rome':      'znver2',
  'x86-milan':     'znver3',
  'arm-thunderx':  'thunderx',
  'arm-thunderx2': 'thunderx2t99',
}

# This gets the generic machine type, e.g. x86_64, i686, aarch64.
# Since uname returns the host machine type, we currently assume that
# cross-compilation is limited to different subarchitectures of the
# generic machine type.  For example, we can cross compile from
# sandybridge to broadwell, but not x86_64 to aarch64.
@memoize
def get_native_machine():
    return platform.uname()[4]

@memoize
def is_known_arm(cpu):
    if cpu.startswith("arm-") or ('aarch64' in cpu) or ('thunderx' in cpu):
        return True
    else:
        return False

# Take a machine name in the same format as uname -m and answer
# whether or not it is in the x86 family.
@memoize
def is_x86_variant(machine):
    if machine.startswith('i') and machine.endswith('86'):  # e.g. i686
        return True
    if machine in ['amd64', 'x64', 'x86', 'x86-64', 'x86_64']:
        return True
    return False

class InvalidLocationError(ValueError):
    pass

# We build the module with the lowest common denominator cpu architecture for
# each platform. This is so that end users can build programs with any
# compatible cpu architecture loaded and we don't have to build a binary for
# each one. However, we put the cpu architecture that the module was built with
# in the gen directory so we need a way to get the proper path no matter what
# cpu architecture is actually loaded. Note that this MUST be kept in sync with
# what we have in the module build script.
def get_module_lcd_cpu(platform_val, cpu):
    if platform_val == "cray-xc":
        if is_known_arm(cpu):
            return "arm-thunderx2"
        else:
            return "sandybridge"
    elif platform_val == "hpe-cray-ex":
        if is_known_arm(cpu):
            return "none"    # we don't know what we need here yet
        else:
            cray_network = os.environ.get('CRAYPE_NETWORK_TARGET', 'none')
            if cray_network.startswith("slingshot") or cray_network == "ofi":
                return "x86-rome"       # We're building on an HPE Cray EX system!
            else:
                return "sandybridge"    # We're still building on an XC.
    elif platform_val == "aarch64":
        return "arm-thunderx"
    else:
        return 'none'

# Adjust the cpu based upon compiler support
@memoize
def adjust_cpu_for_compiler(cpu, flag, get_lcd):
    compiler_val = chpl_compiler.get(flag)
    platform_val = chpl_platform.get(flag)

    isprgenv = flag == 'target' and target_compiler_is_prgenv()

    if isprgenv:
        cray_cpu = os.environ.get('CRAY_CPU_TARGET', 'none')
        if cpu and (cpu != 'none' and cpu != 'unknown' and cpu != cray_cpu):
            warning("Setting the processor type through environment variables "
                    "is not supported for cray-prgenv-*. Please use the "
                    "appropriate craype-* module for your processor type.")
        cpu = cray_cpu
        if cpu == 'none':
            warning("No craype-* processor type module was detected, please "
                    "load the appropriate one if you want any specialization "
                    "to occur.")
        if get_lcd:
            cpu = get_module_lcd_cpu(platform_val, cpu)
            if cpu == 'none':
                warning("Could not detect the lowest common denominator "
                        "processor type for this platform. You may be unable "
                        "to use the Chapel compiler")
        return cpu
    elif 'pgi' in compiler_val:
        return 'none'
    elif 'cray' in compiler_val:
        return 'none'
    elif 'ibm' in compiler_val:
        return 'none'

    return cpu

@memoize
def default_cpu(flag):
    comm_val = chpl_comm.get()
    compiler_val = chpl_compiler.get(flag)
    platform_val = chpl_platform.get(flag)

    cpu = 'unknown'

    if comm_val == 'none' and ('linux' in platform_val or
                               platform_val == 'darwin' or
                               platform_val.startswith('cygwin')):
      # Clang cannot detect the architecture for aarch64.  Otherwise,
      # let the backend compiler do the actual feature set detection. We
      # could be more aggressive in setting a precise architecture using
      # the double checking code above, but it seems like a waste of time
      # to not use the work the backend compilers have already done
      if compiler_val in ['clang', 'llvm']:
          if get_native_machine() == 'aarch64':
              cpu = 'unknown'
          else:
              cpu = 'native'
      else:
            cpu = 'native'

    return cpu

# get_lcd has no effect on non cray systems and is intended to be used to get
# the correct runtime and gen directory.
@memoize
def get(flag, map_to_compiler=False, get_lcd=False):

    cpu_tuple = collections.namedtuple('cpu_tuple', ['flag', 'cpu'])

    if not flag or flag == 'host':
        cpu = overrides.get('CHPL_HOST_CPU', '')
    elif flag == 'target':
        cpu = overrides.get('CHPL_TARGET_CPU', '')
    else:
        raise InvalidLocationError(flag)

    # fast path out for when the user has set arch=none
    if cpu == 'none' or (flag == 'host' and not cpu):
        return cpu_tuple('none', 'none')

    compiler_val = chpl_compiler.get(flag)

    # Adjust cpu for compiler (not all compilers support all cpu
    # settings; PrgEnv might override cpu, etc)
    cpu = adjust_cpu_for_compiler (cpu, flag, get_lcd)

     # support additional cpu synonyms for llvm/gcc/clang
    if map_to_compiler:
        if compiler_val in ('llvm', 'gnu', 'clang'):
            if cpu in cpu_llvm_synonyms:
                cpu = cpu_llvm_synonyms[cpu]

    # Now, if is not yet set, we should set the default.
    if not cpu:
        cpu = default_cpu(flag)

    argname = None
    if cpu and cpu != 'none' and cpu != 'unknown':
        # x86 uses -march= where the others use -mcpu=
        if is_x86_variant(get_native_machine()):
            argname = 'arch'
        else:
            argname = 'cpu'
    else:
        argname = 'none'

    return cpu_tuple(argname or 'none', cpu or 'unknown')


# Returns the default machine.  The flag argument is 'host' or 'target'.
#
# Note that we cannot deduce the machine type corresponding to a given
# architecture.  For example, knowing that we are on a sandybridge does
# not tell us whether we are running in 32-bit mode (sometimes designated
# as i686) or 64-bit mode (x86_64).  For some architectures, the same is
# true of big-endian vs. little-endian mode as well.  Consequently, we have
# insufficient information about the target.  Therefore, the machine type must
# be the same for host and target, though the subarchitecture can be different.
# We can cross compile from sandybridge to broadwell but not from
# x86_64 to aarch64.
#
# Because of all this, the flag argument is unused and we return the same
# answer for host or target.
@memoize
def get_default_machine(flag):
    return get_native_machine()


def _main():
    parser = optparse.OptionParser(usage="usage: %prog [--host|target] "
                                         "[--compflag] [--lcdflag] "
                                         "[--specialize]")
    parser.add_option('--target', dest='flag', action='store_const',
                      const='target', default='target')
    parser.add_option('--host', dest='flag', action='store_const',
                      const='host')
    parser.add_option('--comparch', dest='map_to_compiler',
                      action='store_true', default=False)
    parser.add_option('--compflag', dest='compflag', action='store_true',
                      default=False)
    parser.add_option('--lcdflag', dest = 'get_lcd', action='store_true',
                      default=False)
    (options, args) = parser.parse_args()

    (flag, cpu) = get(options.flag, options.map_to_compiler,
                      options.get_lcd)

    if options.compflag:
        sys.stdout.write("{0}=".format(flag))
    sys.stdout.write("{0}\n".format(cpu))

if __name__ == '__main__':
    _main()
