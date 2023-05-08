from geant4_pybind import *
from geant4_pybind import G4Event, G4Step, G4TouchableHistory, G4VPhysicalVolume
import sys

# Создание геометрии

class ScreenGeometry(G4VUserDetectorConstruction):
    def __init__(self) -> None:
        super().__init__()

    def Construct(self) -> G4VPhysicalVolume:
        
        nist = G4NistManager.Instance()
        checkOverlaps = True

        # World

        world_sizeXY = 30*cm
        world_sizeZ = 30*cm
        world_mat = nist.FindOrBuildMaterial('G4_AIR')

        solid_world = G4Box("World", 0.5*world_sizeXY, 0.5*world_sizeXY, 0.5*world_sizeZ)
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


        # Материалы экранов
        screen_detXY = 250*mm
        screen1_detZ = 1.5*mm
        screen2_detZ = 0.5*mm
        screen3_detZ = 1.5*mm

        # Al
        mat1 = nist.FindOrBuildMaterial('G4_Al')
        # W
        mat2 = nist.FindOrBuildMaterial('G4_W')
        #Ni
        mat3 = nist.FindOrBuildMaterial('G4_Ni')


        #Положение экранов
        screen_coord = 5*mm

        # Первый экран
        self.solid_screen1 = G4Box('Screen1', 0.5*screen_detXY, 0.5*screen_detXY, 0.5*screen1_detZ)
        self.logic_screen1 = G4LogicalVolume(self.solid_screen1, mat1, "Screen1")
        self.phys_screen1 = G4PVPlacement(
            None,
            G4ThreeVector(0, 0, screen_coord),
            self.logic_screen1,
            "Screen1",
            logic_world,
            0,
            checkOverlaps
        )

        # Второй экран
        self.solid_screen2 = G4Box("Screen2", 0.5*screen_detXY, 0.5*screen_detXY, 0.5*screen2_detZ)
        self.logic_screen2 = G4LogicalVolume(self.solid_screen2, mat2, "Screen2")
        self.phys_screen2 = G4PVPlacement(
            None,
            G4ThreeVector(0, 0, screen_coord + screen1_detZ*0.5+0.5*screen2_detZ),
            self.logic_screen2,
            "Screen2",
            logic_world,
            0,
            checkOverlaps
        )

        # Третий экран
        self.solid_screen3 = G4Box("Screen3", 0.5*screen_detXY, 0.5*screen_detXY, 0.5*screen3_detZ)
        self.logic_screen3 = G4LogicalVolume(self.solid_screen3, mat3, "Screen3")
        self.phys_screen3 = G4PVPlacement(
            None,
            G4ThreeVector(0, 0, screen_coord + 0.5*screen1_detZ + screen2_detZ + 0.5*screen3_detZ),
            self.logic_screen3,
            "Screen3",
            logic_world,
            0,
            checkOverlaps
        )

        return phys_world
        

class ScreenDetector(G4VSensitiveDetector):

    def __init__(self, arg0):
        super().__init__(arg0)

    def ProcessHits(self, arg0: G4Step, arg1: G4TouchableHistory) -> bool:
        pass

# Генерация частиц

class PrimaryGeneration(G4VUserPrimaryGeneratorAction):
    def __init__(self) -> None:
        super().__init__()

        n_particles = 20
        self.fParticleGun = G4ParticleGun(n_particles)

        particle_table = G4ParticleTable.GetParticleTable()
        particle = particle_table.FindAntiParticle("proton")
        
        self.fParticleGun.SetParticleDefinition(particle)
        self.fParticleGun.SetParticleMomentumDirection(G4ThreeVector(0., 0., 1.0))
        self.fParticleGun.SetParticleEnergy(50*MeV)
    
    def GeneratePrimaries(self, anEvent: G4Event) -> None:
        
        worldLV = G4LogicalVolumeStore.GetInstance().GetVolume("World")
        world_box = None
        world_half_len = None

        if worldLV != None:
            world_box = worldLV.GetSolid()
        
        if world_box != None:
            world_half_len = world_box.GetZHalfLength() # эту команду vs code не видит
        
        self.fParticleGun.SetParticlePosition(G4ThreeVector(0, 0, -15*cm))
        self.fParticleGun.GeneratePrimaryVertex(anEvent)


class ActionInitialization(G4VUserActionInitialization):

    def Build(self) -> None:
        self.SetUserAction(PrimaryGeneration())


runManager: G4RunManager = G4RunManagerFactory.CreateRunManager(G4RunManagerType.Serial)

runManager.SetUserInitialization(ScreenGeometry())

physics = QBBC()
physics.SetVerboseLevel(1)

runManager.SetUserInitialization(physics)

runManager.SetUserInitialization(ActionInitialization())

runManager.Initialize()

ui = G4UIExecutive(len(sys.argv), sys.argv)
visManager = G4VisExecutive()
visManager.Initialize()

UImanager = G4UImanager.GetUIpointer()
UImanager.ApplyCommand('/control/execute init_vis.mac')
ui.SessionStart()
