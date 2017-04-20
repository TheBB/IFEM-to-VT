import h5py
from collections import namedtuple
from io import StringIO
from itertools import chain, product
import logging
import numpy as np
import splipy.IO
from splipy.SplineModel import ObjectCatalogue


class G2Object(splipy.IO.G2):

    def __init__(self, fstream, mode):
        self.fstream = fstream
        self.onlywrite = mode == 'w'
        super(G2Object, self).__init__('')

    def __enter__(self):
        return self


Field = namedtuple('Field', ['name', 'basis', 'ncomps'])
Basis = namedtuple('Basis', ['name', 'updates'])


class Reader:

    def __init__(self, filename):
        self.filename = filename
        self.patch_cache = {}

    def __enter__(self):
        self.h5 = h5py.File(self.filename, 'r')
        self.check()
        self.catalogue = ObjectCatalogue(self.max_pardim, interior=False)
        return self

    def __exit__(self, type_, value, backtrace):
        self.h5.close()

    def write(self, w):
        for lid, time, lgrp in self.times():
            for basis in self.bases.values():
                if lid in basis.updates:
                    self.write_geometry(w, lid, basis)
            w.add_time(time)

    def write_geometry(self, w, lid, basis):
        geometry = []
        for pid in range(self.npatches(lid, basis)):
            patch = self.patch(lid, basis, pid)
            node = self.catalogue.add(patch).node
            if not hasattr(node, 'patchid'):
                node.patchid = None
            if not hasattr(node, 'last_written'):
                node.last_written = -1

            if node.last_written >= lid:
                logging.debug('Skipping update for %s, level %d, patch %d (already written)',
                              basis.name, lid, pid)
                continue
            node.last_written = lid

            patch = node.obj
            node.tesselation = patch.knots()
            nodes = patch(*node.tesselation)

            # Elements
            ranges = [range(k-1) for k in nodes.shape[:-1]]
            nidxs = [np.array(q) for q in zip(*product(*ranges))]
            eidxs = np.zeros((len(nidxs[0]), 2**len(nidxs)))
            if len(nidxs) == 1:
                eidxs[:,0] = nidxs[0]
                eidxs[:,1] = nidxs[0] + 1
            elif len(nidxs) == 2:
                i, j = nidxs
                eidxs[:,0] = np.ravel_multi_index((i, j), nodes.shape[:-1])
                eidxs[:,1] = np.ravel_multi_index((i+1, j), nodes.shape[:-1])
                eidxs[:,2] = np.ravel_multi_index((i+1, j+1), nodes.shape[:-1])
                eidxs[:,3] = np.ravel_multi_index((i, j+1), nodes.shape[:-1])
            elif len(nidxs) == 3:
                i, j, k = nidxs
                eidxs[:,0] = np.ravel_multi_index((i, j, k), nodes.shape[:-1])
                eidxs[:,1] = np.ravel_multi_index((i+1, j, k), nodes.shape[:-1])
                eidxs[:,2] = np.ravel_multi_index((i+1, j+1, k), nodes.shape[:-1])
                eidxs[:,3] = np.ravel_multi_index((i, j+1, k), nodes.shape[:-1])
                eidxs[:,4] = np.ravel_multi_index((i, j, k+1), nodes.shape[:-1])
                eidxs[:,5] = np.ravel_multi_index((i+1, j, k+1), nodes.shape[:-1])
                eidxs[:,6] = np.ravel_multi_index((i+1, j+1, k+1), nodes.shape[:-1])
                eidxs[:,7] = np.ravel_multi_index((i, j+1, k+1), nodes.shape[:-1])

            logging.debug('Updating geometry for %s, level %d, patch %d',
                          basis.name, lid, pid)
            w.update_geometry(np.ndarray.flatten(nodes), np.ndarray.flatten(eidxs), len(nidxs), node.patchid)

    @property
    def ntimes(self):
        return len(self.h5)

    def times(self):
        for level in range(self.ntimes):
            # FIXME: Grab actual time here as second element
            yield level, float(level), self.h5[str(level)]

    def basis_level(self, level, basis):
        if not isinstance(basis, Basis):
            basis = self.bases[basis]
        try:
            return next(l for l in basis.updates[::-1] if l <= level)
        except StopIteration:
            raise ValueError('Geometry for basis {} unavailable at timestep {}'.format(basis, index))

    def npatches(self, level, basis):
        if not isinstance(basis, Basis):
            basis = self.bases[basis]
        level = self.basis_level(level, basis)
        return len(self.h5['{}/{}/basis'.format(str(level), basis.name)])

    def patch(self, lid, basis, index):
        if not isinstance(basis, Basis):
            basis = self.bases[basis]
        lid = self.basis_level(lid, basis)
        key = (lid, basis.name, index)
        if key not in self.patch_cache:
            g2str = self.h5[
                '{}/{}/basis/{}'.format(str(lid), basis.name, str(index+1))
            ][:].tobytes().decode()
            g2data = StringIO(g2str)
            with G2Object(g2data, 'r') as g:
                patch = g.read()[0]
                patch.set_dimension(3)
                self.patch_cache[key] = patch
        return self.patch_cache[key]

    def check(self):
        self.bases = {}
        self.max_pardim = 0
        for lid, _, lgrp in self.times():
            for basis, bgrp in lgrp.items():
                self.bases.setdefault(basis, Basis(basis, []))
                if 'basis' in bgrp:
                    self.bases[basis].updates.append(lid)
                self.max_pardim = max(self.max_pardim, self.patch(lid, basis, 0).pardim)

        self.fields = {}
        basis_iter = ((lid, basis, bgrp) for basis, bgrp in lgrp.items() for lid, _, lgrp in self.times())
        for lid, basis, bgrp in basis_iter:
            if 'fields' not in bgrp:
                continue
            for field, fgrp in bgrp['fields'].items():
                if field in self.fields:
                    continue
                ncomps = len(fgrp['1']) // len(self.patch(lid, basis, 0))
                self.fields.setdefault(field, Field(field, self.bases[basis], ncomps))
