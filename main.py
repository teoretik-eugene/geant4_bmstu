from geant4_pybind import *
from geant4_pybind import G4VPhysicalVolume
from DataServer import DataServer
from TrimParser import TrimParser

class ScreenGeometry(G4VUserDetectorConstruction):
    def __init__(self, ds: DataServer, tp: TrimParser) -> None:
        super().__init__()
        self.ds = ds
        self.tp = tp

    # TODO реализовать логику создания экранов/слоев из запроса
    def Construct(self) -> G4VPhysicalVolume:
        
        nist = G4NistManager.Instance()
        checkOverlaps = True

        # World

        world_sizeXY = 50 * cm
        world_sizeZ = 50 * cm
        world_mat = nist.FindOrBuildMaterial('G4_AIR')

        solid_world = G4Box("World", 0.5 * world_sizeXY, 0.5 * world_sizeXY, 0.5 * world_sizeZ)
        logic_world = G4LogicalVolume(solid_world, world_mat, "World")

        phys_world = G4PVPlacement(
            None,
            G4ThreeVector(),
            logic_world,
            "World",
            None,
            False,
            0,
            checkOverlaps
        )

        # Логика формирования экранов, по хорошему, конечно, стоит вынести это в отдельный метод
        materials = self.tp.readMaterials()
        # 9 task as an example !
        material_list = []
        for mat in materials:
            material_name = mat.get('Name')
            material_width = float(mat.get('Width'))

            elements = mat.get('Elements')
            element_list = []
            for elem in elements:
                elem_name = elem.get('Name')
                elem_symbol = elem.get('Symbol')
                elem_atomic_number = float(elem.get('Atomic_number'))
                elem_density = float(elem.get('Density'))
                elem_aem = float(elem.get('Standard_atomic_weight'))
                elem_percentage = float(elem.get('Percentage'))
                
                # Но тут можно попробовать и создавать стандартные элементы типа G4_ название
                # с большой буквы
                element = G4Element(name=elem_name, symbol=elem_symbol, 
                                    Zeff=elem_atomic_number, Aeff=elem_aem*g/mole)
                element_list.append([element, elem_percentage, elem_density])    # вот тут нужно процент высчитать
            
            # Высчитывание процента
            # Ром, тут ты делай красиво, я в вашем питоне не шарю
            total = 0
            for _ in element_list:
                total += _[1]
            
            avg_density = 0     # плотность материала вычисляется как сумма плотностей с множителями
            for _ in element_list:
                _[1] = _[1] / total
                avg_density += _[1] * _[2]

            material = G4Material(name=material_name, density=avg_density*g/cm3, 
                                  nComponents=len(element_list))
            
            for _ in element_list:
                material.AddElement(_[0], frac=_[1])

            material_list.append(tuple([material, material_width, material_name]))
            print(material)

        # Размеры экранов
        screen_detXY = 250 * mm
        screen_coord = 15 * mm

        # Создание непосредственно экранов:
        num = 0
        for mat in material_list:
            screen_name = f'Screen - {num} -- {mat[2]}'
            screen_width = mat[1] / 1000 * mm
            num += 1
            solid_screen = G4Box(screen_name, 0.5 * screen_detXY, 0.5 * screen_detXY, 0.5 * screen_width)





if __name__ == '__main__':
    # Создаем объект класса, который будет общаться с серваком
    ds = DataServer()
    tp = TrimParser(data=ds.get_current_task_to_json(9))
    sc = ScreenGeometry(ds, tp)
    sc.Construct()

