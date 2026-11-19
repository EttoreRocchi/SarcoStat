# Examples

## Example micrographs

Two transmission electron micrographs of thin longitudinal sections of human skeletal muscle (JEOL 100CX TEM), to try the GUI:

| File | Shows | Scale |
|------|-------|-------|
| `human_skeletal_muscle_tem_1.jpg` | Several myofibrils with Z-lines in register across a long tract; suited to the myofibril mode (linearity and register). | 2 µm bar = 302 px, so 1 px = 6.62 nm |
| `human_skeletal_muscle_tem_3.jpg` | Stretched sarcomeres with wide I-bands; the Z-lines of adjacent myofibrils are staggered, so each Z-line ends where its myofibril ends. Suited to the single-sarcomere mode. | 1 µm bar = 228 px, so 1 px = 4.39 nm |

To calibrate, enter the pixel size and the unit in the toolbar (for example `Scale: 1 px = 6.62 nm`) and press Apply.

**Author and licence.** Both images are by Louisa Howard, Dartmouth Electron Microscope Facility, Dartmouth College, and are in the public domain. The facility's image index states: "These images are in the public domain. Do with them what you will." ([archived copy, 2013](https://web.archive.org/web/20130104130114/http://remf.dartmouth.edu/imagesindex.html)). The files are unmodified copies from Wikimedia Commons ([image 1](https://commons.wikimedia.org/wiki/File:Human_skeletal_muscle_tissue_1_-_TEM.jpg), [image 3](https://commons.wikimedia.org/wiki/File:Human_skeletal_muscle_tissue_3_-_TEM.jpg)), where they are marked `PD-author`. The scales were measured on the scale bars in the bottom strip of each image.

## Configuration files

`configs/` holds small simulation configurations used by the documentation and the smoke tests (see [docs/simulation.md](../docs/simulation.md)).
