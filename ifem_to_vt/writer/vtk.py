from itertools import chain
from os import makedirs
from pathlib import Path

import numpy as np
from singledispatchmethod import singledispatchmethod
import treelog as log
import vtk
import vtk.util.numpy_support as vnp

from dataclasses import dataclass

from typing import Optional, Dict
from ..typing import Array2D

from .. import config
from ..fields import AbstractFieldPatch, CombinedFieldPatch, SimpleFieldPatch
from ..geometry import Patch, UnstructuredPatch, Hex
from ..util import ensure_ncomps
from .writer import Writer



@dataclass
class Field:
    cells: bool
    data: Dict[int, Array2D]


class VTKWriter(Writer):

    writer_name = "VTK"

    patches: Dict[int, UnstructuredPatch]
    fields: Dict[str, Field]

    @classmethod
    def applicable(cls, fmt: str) -> bool:
        return fmt == 'vtk'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.patches = dict()
        self.fields = dict()

    def nan_filter(self, data: Array2D) -> Array2D:
        I, J = np.where(np.isnan(data))
        if len(I) > 0 and config.output_mode == 'ascii':
            log.warning("VTK ASCII files do not support NaN, will be set to zero")
            data[I, J] = 0.0
        return data

    def validate_mode(self):
        if not config.output_mode in ('ascii', 'binary'):
            raise ValueError(f"VTK format does not support '{config.output_mode}' mode")

    def get_writer(self):
        writer = vtk.vtkUnstructuredGridWriter()
        if config.output_mode == 'ascii':
            writer.SetFileTypeToASCII()
        else:
            writer.SetFileTypeToBinary()
        return writer

    @singledispatchmethod
    def update_geometry(self, patch: Patch, patchid: Optional[int] = None):
        if patchid is None:
            patchid = super().update_geometry(patch)
        return self.update_geometry(patch.tesselate(), patchid=patchid)

    @update_geometry.register(UnstructuredPatch)
    def _(self, patch: UnstructuredPatch, patchid: Optional[int] = None):
        if patchid is None:
            patchid = super().update_geometry(patch)
        self.patches[patchid] = patch

    def update_field(self, field: AbstractFieldPatch):
        patchid = super().update_field(field)
        field.ensure_ncomps(3, allow_scalar=True)
        data = field.tesselate()
        self.fields.setdefault(field.name, Field(field.cells, dict())).data[patchid] = self.nan_filter(data)

    def finalize_step(self):
        super().finalize_step()
        grid = vtk.vtkUnstructuredGrid()

        # Concatenate nodes of all patches
        allpoints = np.vstack([p.nodes for p in self.patches.values()])
        allpoints = ensure_ncomps(allpoints, 3, allow_scalar=False)
        points = vtk.vtkPoints()
        points.SetData(vnp.numpy_to_vtk(allpoints))
        grid.SetPoints(points)

        # Concatenate cells of all patches
        patches = self.patches.values()
        offset = chain([0], np.cumsum([p.num_nodes for p in patches]))
        cells = np.vstack([patch.cells + off for patch, off in zip(patches, offset)])
        cells = np.hstack([cells.shape[-1] * np.ones((cells.shape[0], 1), dtype=int), cells])

        cellarray = vtk.vtkCellArray()
        cellarray.SetCells(len(cells), vnp.numpy_to_vtkIdTypeArray(cells.ravel(), deep=True))

        patch = next(iter(patches))
        celltype = vtk.VTK_HEXAHEDRON if isinstance(patch.celltype, Hex) else vtk.VTK_QUAD
        grid.SetCells(celltype, cellarray)

        pointdata = grid.GetPointData()
        celldata = grid.GetCellData()

        for name, field in self.fields.items():
            data = np.vstack([k for k in field.data.values()])
            array = vnp.numpy_to_vtk(data)
            array.SetName(name)
            if field.cells:
                celldata.AddArray(array)
            else:
                pointdata.AddArray(array)

        filename = self.make_filename(with_step=True)
        writer = self.get_writer()
        writer.SetFileName(str(filename))
        writer.SetInputData(grid)
        writer.Write()

        log.user(filename)


class VTUWriter(VTKWriter):

    writer_name = "VTU"

    @classmethod
    def applicable(cls, fmt: str) -> bool:
        return fmt == 'vtu'

    def nan_filter(self, results):
        return results

    def validate_mode(self):
        if not config.output_mode in ('appended', 'ascii', 'binary'):
            raise ValueError("VTU format does not support '{}' mode".format(self.config.output_mode))

    def get_writer(self):
        writer = vtk.vtkXMLUnstructuredGridWriter()
        if config.output_mode == 'appended':
            writer.SetDataModeToAppended()
        elif config.output_mode == 'ascii':
            writer.SetDataModeToAscii()
        elif config.output_mode == 'binary':
            writer.SetDataModeToBinary()
        return writer


class PVDWriter(VTUWriter):

    writer_name = "PVD"

    @classmethod
    def applicable(self, fmt: str) -> bool:
        return fmt == 'pvd'

    def __init__(self, outpath: Path):
        self.rootfile = outpath
        super().__init__(outpath.with_suffix('') / 'data.vtu')

    def __enter__(self):
        super().__enter__()
        self.pvd = open(self.rootfile, 'w')
        self.pvd.write('<VTKFile type="Collection">\n')
        self.pvd.write('  <Collection>\n')
        return self

    def __exit__(self, type_, value, backtrace):
        super().__exit__(type_, value, backtrace)
        self.pvd.write('  </Collection>\n')
        self.pvd.write('</VTKFile>\n')
        self.pvd.close()

    def make_filename(self, *args, **kwargs):
        filename = super().make_filename(*args, **kwargs)
        makedirs(filename.parent, mode=0o775, exist_ok=True)
        return filename

    def finalize_step(self):
        super().finalize_step()
        filename = self.make_filename(with_step=True)
        if self.stepdata:
            timestep = next(iter(self.stepdata.values()))
        else:
            timestep = self.stepid
        self.pvd.write('    <DataSet timestep="{}" part="0" file="{}" />\n'.format(timestep, filename))
