from geant4_pybind import *
from geant4_pybind import G4Step, G4TouchableHistory, G4VPhysicalVolume
from DataServer import DataServer
from TrimParser import TrimParser
import sys

class ScreenGeometry(G4VUserDetectorConstruction):
    def __init__(self, task_id: int) -> None:
        super().__init__()
        self.ds = ds = DataServer()
        self.tp = TrimParser(data=ds.get_current_task_to_json(task_id))

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
        
        material_list = []
        for mat in materials:
            mat_name = mat.get('Name')
            mat_width = (float(mat.get('Width')) / 1000) * mm
            
            elements = mat.get('Elements')
            element_list = []
            
            for elem in elements:
                elem_name = elem.get('Name')
                elem_symbol = elem.get('Symbol')
                elem_atomic_number = float(elem.get('Atomic_number'))
                elem_density = float(elem.get('Density'))
                elem_aem = float(elem.get('Standard_atomic_weight'))
                elem_perc = float(elem.get('Percentage'))
                #мб попробовать другое название вбивать?
                #element = nist.FindOrBuildElement(elem_atomic_number, False)
                element = nist.FindOrBuildElement(elem_symbol)
                #print(f'elem: {element}')
                element_list.append([element, elem_perc, elem_density])
            
            # Высчитывание процента
            total_perc = 0
            # Общий относительный процент
            for _ in element_list:
                total_perc += _[1]
            
            avg_density = 0
            for _ in element_list:
                # elem_perc = elem_perc / total
                _[1] = _[1] / total_perc
                # avg_density = elem_perc1 * elem_density1 + elem_perc2 * elem_density2
                avg_density += _[1] * _[2]
            
            components = len(element_list)
            material = G4Material(mat_name, avg_density*g/cm3, components)
            for _ in element_list:
                material.AddElement(_[0], frac=_[1])
            print(material.GetName())
            material_list.append([material, mat_width, mat_name])

        
        screen_detXY = 250 * mm
        screen_coord = 15 * mm

        self.screen_list = []
        num = 0
        for mat in material_list:
            screen_name = f'Screen-{num}--{mat[2]}'
            screen_width = mat[1]
            screen_material = mat[0]
            num += 1
            solid_screen = G4Box(screen_name, 0.5*screen_detXY, 0.5*screen_detXY, 0.5*screen_width)
            logic_screen = G4LogicalVolume(solid_screen, screen_material, screen_name)
            phys_screen = G4PVPlacement(
                None,
                G4ThreeVector(0, 0, screen_coord + 0.5 * screen_width),
                logic_screen,
                screen_name,
                logic_world,
                0,
                checkOverlaps
            )
            screen_coord += screen_width
            self.screen_list.append(phys_screen)


        return phys_world
    
class ScreenSensitiveDetector(G4VSensitiveDetector):

    def __init__(self, name: str):
        super().__init__(name)

    def ProcessHits(self, arg0: G4Step, arg1: G4TouchableHistory) -> bool:
        
        
        return True


class PrimaryGeneration(G4VUserPrimaryGeneratorAction):
    def __init__(self) -> None:
        super().__init__()

        n_particles = 1
        self.fParticleGun = G4ParticleGun(n_particles)

        particle_table = G4ParticleTable.GetParticleTable()
        particle = particle_table.FindAntiParticle("proton")

        self.fParticleGun.SetParticleDefinition(particle)
        self.fParticleGun.SetParticleMomentumDirection(G4ThreeVector(0., 0., 1.0))
        self.fParticleGun.SetParticleEnergy(4 * MeV)

    def GeneratePrimaries(self, anEvent: G4Event) -> None:

        worldLV = G4LogicalVolumeStore.GetInstance().GetVolume("World")
        world_box = None
        world_half_len = None

        if worldLV != None:
            world_box = worldLV.GetSolid()

        if world_box != None:
            world_half_len = world_box.GetZHalfLength()  # эту команду vs code не видит

        self.fParticleGun.SetParticlePosition(G4ThreeVector(0, 0, -5*mm ))
        self.fParticleGun.GeneratePrimaryVertex(anEvent)

class ActionInitialization(G4VUserActionInitialization):

    def Build(self) -> None:
        #self.SetUserAction(SteppingAction())
        self.SetUserAction(PrimaryGeneration())

if __name__ == '__main__':
    # Создаем объект класса, который будет общаться с серваком
    #ds = DataServer()
    #tp = TrimParser(data=ds.get_current_task_to_json(9))
    task_id = 170
    
    runManager: G4RunManager = G4RunManagerFactory.CreateRunManager(G4RunManagerType.Serial)
    
    runManager.SetUserInitialization(ScreenGeometry(task_id=task_id))
    
    physics = FTFP_BERT()
    physics.SetVerboseLevel(1)
    runManager.SetUserInitialization(physics)

    runManager.SetUserInitialization(ActionInitialization())

    runManager.Initialize()
    #runManager.BeamOn(10)

    ui = G4UIExecutive(len(sys.argv), sys.argv)
    visManager = G4VisExecutive()
    visManager.Initialize()

    UImanager = G4UImanager.GetUIpointer()
    UImanager.ApplyCommand('/control/execute init_vis.mac')
    UImanager.ApplyCommand('/gun/particle proton')
    UImanager.ApplyCommand('/gun/energy 40 MeV')
    UImanager.ApplyCommand('/tracking/verbose 0')
    UImanager.ApplyCommand('/run/beamOn 50')
    ui.SessionStart()
    sys.exit()


