#!/usr/bin/env python
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

'''
Non-relativistic Restricted Kohn-Sham for periodic systems with k-point sampling

See Also:
    pyscf.pbc.dft.rks.py : Non-relativistic Restricted Kohn-Sham for periodic
                           systems at a single k-point
'''

import time
import numpy as np
from pyscf import lib
from pyscf.lib import logger
from pyscf.pbc.scf import kuhf
from pyscf.pbc.dft import gen_grid
from pyscf.pbc.dft import numint
from pyscf.pbc.dft import rks

def get_veff(ks, cell=None, dm=None, dm_last=0, vhf_last=0, hermi=1,
             kpts=None, kpts_band=None):
    '''Coulomb + XC functional for UKS.  See pyscf/pbc/dft/uks.py
    :func:`get_veff` fore more details.
    '''
    if cell is None: cell = ks.cell
    if dm is None: dm = ks.make_rdm1()
    if kpts is None: kpts = ks.kpts
    t0 = (time.clock(), time.time())
    if ks.grids.coords is None:
        ks.grids.build(with_non0tab=True)
        small_rho_cutoff = ks.small_rho_cutoff
        t0 = logger.timer(ks, 'setting up grids', *t0)
    else:
        small_rho_cutoff = 0

    if hermi == 2:  # because rho = 0
        n, exc, vxc = (0,0), 0, 0
    else:
        n, exc, vxc = ks._numint.nr_uks(cell, ks.grids, ks.xc, dm, 0,
                                        kpts, kpts_band)
        logger.debug(ks, 'nelec by numeric integration = %s', n)
        t0 = logger.timer(ks, 'vxc', *t0)

    # ndim = 4 : dm.shape = ([alpha,beta], nkpts, nao, nao)
    ground_state = (dm.ndim == 4 and dm.shape[0] == 2 and kpts_band is None)
    weight = 1./len(kpts)

    hyb = ks._numint.hybrid_coeff(ks.xc, spin=cell.spin)
    if abs(hyb) < 1e-10:
        vj = ks.get_j(cell, dm, hermi, kpts, kpts_band)
        vxc += vj[0] + vj[1]
    else:
        vj, vk = ks.get_jk(cell, dm, hermi, kpts, kpts_band)
        vxc += vj[0] + vj[1] - vk * hyb

        if ground_state:
            exc -= (np.einsum('Kij,Kji', dm[0], vk[0]) +
                    np.einsum('Kij,Kji', dm[1], vk[1])).real * hyb * .5 * weight

    if ground_state:
        ecoul = np.einsum('Kij,Kji', dm[0]+dm[1], vj[0]+vj[1]).real * .5 * weight
    else:
        ecoul = None

    vxc = lib.tag_array(vxc, ecoul=ecoul, exc=exc, vj=None, vk=None)

    nelec = cell.nelec
    if (small_rho_cutoff > 1e-20 and ground_state and
        abs(n[0]-nelec[0]) < 0.01*n[0] and abs(n[1]-nelec[1]) < 0.01*n[1]):
        # Filter grids the first time setup grids
        idx = ks._numint.large_rho_indices(cell, dm, ks.grids,
                                           small_rho_cutoff, kpts)
        logger.debug(ks, 'Drop grids %d',
                     ks.grids.weights.size - np.count_nonzero(idx))
        ks.grids.coords  = np.asarray(ks.grids.coords [idx], order='C')
        ks.grids.weights = np.asarray(ks.grids.weights[idx], order='C')
        ks.grids.non0tab = ks.grids.make_mask(cell, ks.grids.coords)
    return vxc


class KUKS(kuhf.KUHF):
    '''RKS class adapted for PBCs with k-point sampling.
    '''
    def __init__(self, cell, kpts=np.zeros((1,3))):
        kuhf.KUHF.__init__(self, cell, kpts)
        self.xc = 'LDA,VWN'
        self.grids = gen_grid.UniformGrids(cell)
        self.small_rho_cutoff = 1e-7  # Use rho to filter grids
##################################################
# don't modify the following attributes, they are not input options
        # Note Do not refer to .with_df._numint because gs/coords may be different
        self._numint = numint._KNumInt(kpts)
        self._keys = self._keys.union(['xc', 'grids', 'small_rho_cutoff'])

    def dump_flags(self):
        kuhf.KUHF.dump_flags(self)
        logger.info(self, 'XC functionals = %s', self.xc)
        self.grids.dump_flags()

    get_veff = get_veff

    def energy_elec(self, dm_kpts=None, h1e_kpts=None, vhf=None):
        if h1e_kpts is None: h1e_kpts = self.get_hcore(self.cell, self.kpts)
        if dm_kpts is None: dm_kpts = self.make_rdm1()
        if vhf is None or getattr(vhf, 'ecoul', None) is None:
            vhf = self.get_veff(self, self.cell, dm_kpts)

        weight = 1./len(h1e_kpts)
        e1 = weight *(np.einsum('kij,kji', h1e_kpts, dm_kpts[0]) +
                      np.einsum('kij,kji', h1e_kpts, dm_kpts[1])).real
        tot_e = e1 + vhf.ecoul + vhf.exc
        logger.debug(self, 'E1 = %s  Ecoul = %s  Exc = %s', e1, vhf.ecoul, vhf.exc)
        return tot_e, vhf.ecoul + vhf.exc

    density_fit = rks._patch_df_beckegrids(kuhf.KUHF.density_fit)
    mix_density_fit = rks._patch_df_beckegrids(kuhf.KUHF.mix_density_fit)