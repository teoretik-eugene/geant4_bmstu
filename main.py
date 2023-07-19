from geant4_pybind import *
from geant4_pybind import G4VPhysicalVolume
from DataServer import DataServer
from TrimParser import TrimParser
import sys

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

        '''
            Возникает ошибка: Segmentation fault (core dumped)
        '''
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
            # @Ром, тут ты делай красиво, я в вашем питоне не шарю
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
        # Перебираем все материалы и для каждого материала создаем экран
        screen_list = []    # лист экранов, будем потом передавать этот лист
        num = 0
        print(f'mat len: {len(material_list)}')
        for mat in material_list:
            screen_name = f'Screen-{num} -- {mat[2]}'
            screen_width = mat[1] / 1000 * mm
            screen_mat = mat[0]
            num += 1
            solid_screen = G4Box(screen_name, 0.5 * screen_detXY, 0.5 * screen_detXY, 0.5 * screen_width)
            logic_screen = G4LogicalVolume(solid_screen, screen_mat, screen_name)
            phys_screen = G4PVPlacement(
                None, 
                G4ThreeVector(0, 0, screen_coord + 0.5*screen_width),   # раставляем вдоль оси z
                logic_screen,
                screen_name,
                logic_world,
                0,
                checkOverlaps
            )
            # Перемещаем координату экрана на величину толщины экрана
            screen_coord += screen_width
            screen_list.append(phys_screen)

        return phys_world
    

class PrimaryGeneration(G4VUserPrimaryGeneratorAction):
    def __init__(self) -> None:
        super().__init__()

        n_particles = 1
        self.fParticleGun = G4ParticleGun(n_particles)

        particle_table = G4ParticleTable.GetParticleTable()
        particle = particle_table.FindAntiParticle("proton")

        self.fParticleGun.SetParticleDefinition(particle)
        self.fParticleGun.SetParticleMomentumDirection(G4ThreeVector(0., 0., 1.0))
        self.fParticleGun.SetParticleEnergy(50 * MeV)

    def GeneratePrimaries(self, anEvent: G4Event) -> None:

        worldLV = G4LogicalVolumeStore.GetInstance().GetVolume("World")
        world_box = None
        world_half_len = None

        if worldLV != None:
            world_box = worldLV.GetSolid()

        if world_box != None:
            world_half_len = world_box.GetZHalfLength()  # эту команду vs code не видит

        self.fParticleGun.SetParticlePosition(G4ThreeVector(0, 0, -5 * cm))
        self.fParticleGun.GeneratePrimaryVertex(anEvent)

class ActionInitialization(G4VUserActionInitialization):

    def Build(self) -> None:
        #self.SetUserAction(SteppingAction())
        self.SetUserAction(PrimaryGeneration())

if __name__ == '__main__':
    # Создаем объект класса, который будет общаться с серваком
    ds = DataServer()
    tp = TrimParser(data=ds.get_current_task_to_json(9))
    
    runManager: G4RunManager = G4RunManagerFactory.CreateRunManager(G4RunManagerType.Serial)
    
    runManager.SetUserInitialization(ScreenGeometry(ds=ds, tp=tp))

    physics = FTFP_BERT()
    physics.SetVerboseLevel(1)

    runManager.SetUserInitialization(physics)

    runManager.SetUserInitialization(ActionInitialization())

    runManager.Initialize()
    runManager.BeamOn(10)


