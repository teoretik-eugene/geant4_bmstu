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
            G4ThreeVector(0, 0, screen_coord + screen1_detZ*0.5+ 0.5*screen2_detZ),
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
        
    def ConstructSDandField(self) -> None:
        
        fSDM = G4SDManager.GetSDMpointer()

        # Объявляем экраны чувствительными
        screen1_detector_name = "Screen1_Detector"
        screen1_detector = ScreenDetector(screen1_detector_name)

        screen2_detector_name = "Screen2_Detector"
        screen2_detector = ScreenDetector(screen2_detector_name)

        screen3_detector_name = "Screen3_Detector"
        screen3_detector = ScreenDetector(screen3_detector_name)

        # Добавляем их в менеджер детекторов
        fSDM.AddNewDetector(screen1_detector)
        self.logic_screen1.SetSensitiveDetector(screen1_detector)

        fSDM.AddNewDetector(screen2_detector)
        self.logic_screen2.SetSensitiveDetector(screen2_detector)

        fSDM.AddNewDetector(screen3_detector)
        self.logic_screen3.SetSensitiveDetector(screen3_detector)


class ScreenDetector(G4VSensitiveDetector):

    def __init__(self, name):
        super().__init__(name)

    def ProcessHits(self, aStep: G4Step, hist: G4TouchableHistory) -> bool:
        edep = aStep.GetTotalEnergyDeposit()     # не понял че за энергия   

        track = aStep.GetTrack()

        kinetic = aStep.GetTrack().GetKineticEnergy()

        if kinetic == 0 and track.GetDefinition().GetParticleName() == 'proton':
            print(f'{track.GetDefinition().GetParticleName()}\'s kinetic 0 in \
                  {track.GetVolume().GetName()} coord: {track.GetPosition().z/mm}')

        if edep == 0:
            return False
        
        newHit = TrackerHit(aStep.GetTrack().GetTrackID,
                            edep,
                            aStep.GetPostStepPoint().GetPosition(),
                            aStep.GetPostStepPoint().GetKineticEnergy())
        return True

class TrackerHit(G4VHit):
    
    def __init__(self, trackID, edep, pos, kinetic) -> None:
        super().__init__()
        self.fTrackID = trackID
        self.fEdep = edep
        self.fPos = pos
        self.fKinetic = kinetic
    
    def Draw(self) -> None:
        vVisManager = G4VVisManager.GetConcreteInstance()
        if vVisManager != None:
            circle = G4Circle(self.fPos)
            circle.SetScreenSize(4)
            circle.SetFillStyle(G4Circle.filled)
            colour = G4Colour(1, 0, 0)
            attribs = G4VisAttributes()
            attribs.SetColor(colour)
            circle.SetVisAttributes(attribs)
            vVisManager.Draw(circle)
            
    
    def Print(self) -> None:
        print(f"TrackID: {self.fTrackID}  =====  Edep: {self.fEdep}")
        #тут еще в примере что-то было


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
        
        self.fParticleGun.SetParticlePosition(G4ThreeVector(0, 0, -5*cm))
        self.fParticleGun.GeneratePrimaryVertex(anEvent)


class ActionInitialization(G4VUserActionInitialization):

    def Build(self) -> None:
        self.SetUserAction(PrimaryGeneration())


runManager: G4RunManager = G4RunManagerFactory.CreateRunManager(G4RunManagerType.Serial)

runManager.SetUserInitialization(ScreenGeometry())

physics = FTFP_BERT()
physics.SetVerboseLevel(1)

runManager.SetUserInitialization(physics)

runManager.SetUserInitialization(ActionInitialization())

runManager.Initialize()
#runManager.BeamOn(100)

ui = G4UIExecutive(len(sys.argv), sys.argv)
visManager = G4VisExecutive()
visManager.Initialize()

UImanager = G4UImanager.GetUIpointer()
UImanager.ApplyCommand('/control/execute init_vis.mac')
UImanager.ApplyCommand('/gun/particle proton')
UImanager.ApplyCommand('/gun/energy 50 MeV')
UImanager.ApplyCommand('/tracking/verbose 0')
UImanager.ApplyCommand('/run/beamOn 500')
ui.SessionStart()
