from geant4_pybind import *
from geant4_pybind import G4Event, G4Step, G4TouchableHistory, G4VPhysicalVolume
import sys


class MyDetectorConstructor(G4VUserDetectorConstruction):
    def __init__(self) -> None:
        super().__init__()

    def Construct(self) -> G4VPhysicalVolume:
        nist = G4NistManager.Instance()        

        det_sizeXY = 25*cm
        det_sizeZ = 0.15*cm
        det_mat = nist.FindOrBuildMaterial("G4_W")

        checkOverlaps = True

        world_sizeXY = 30*cm
        world_sizeZ = 20*cm
        world_mat = nist.FindOrBuildMaterial("G4_AIR")

        solid_world = G4Box("World",
                            0.5*world_sizeXY, 0.5*world_sizeXY, 0.5*world_sizeZ)
        
        logic_world = G4LogicalVolume(solid_world, world_mat, "World")

        phys_world = G4PVPlacement(None,
            G4ThreeVector(),
            logic_world,
            "World",
            None,
            False,
            0,
            checkOverlaps
        )

        solid_det = G4Box("Detector", 0.5*det_sizeXY, 0.5*det_sizeXY, 0.5*det_sizeZ)

        logic_det = G4LogicalVolume(solid_det, det_mat, "Detector")

        phys_det = G4PVPlacement(None,
                                 G4ThreeVector(0, 0, 5*cm),
                                 logic_det,
                                 "Detector",
                                 logic_world,
                                 False,
                                 0,
                                 checkOverlaps
                                 )
        
        self.logic_tar = G4LogicalVolume(solid_det, det_mat, "Target")
        phys_tar = G4PVPlacement(None,
                                 G4ThreeVector(0, 0, -5*cm),
                                 self.logic_tar,
                                 "Target",
                                 logic_world,
                                 0,
                                 checkOverlaps)
        return phys_world
    
    def ConstructSDandField(self) -> None:
        detector_name = "DetectorSD"
        detector = MyDetector(detector_name)

        fSDM = G4SDManager.GetSDMpointer()
        fSDM.AddNewDetector(detector)
        self.logic_tar.SetSensitiveDetector(detector)


class MyDetector(G4VSensitiveDetector):

    def __init__(self, arg0):
        super().__init__(arg0)


    def ProcessHits(self, aStep: G4Step, hist: G4TouchableHistory) -> bool:
        track: G4Track = aStep.GetTrack()

        preStepPoint: G4StepPoint = aStep.GetPreStepPoint()
        postStepPoint: G4StepPoint = aStep.GetPostStepPoint()

        posProton = preStepPoint.GetPosition()
        energy = preStepPoint.GetKineticEnergy()

        print(f'Proton pos: {posProton} --- Proton energy: {energy}')

        return True


class MyPrimaryGenerationAction(G4VUserPrimaryGeneratorAction):
    
    def __init__(self) -> None:
        super().__init__()
        self.fEnvelopeBox: G4VSolid = None
        n_particle = 1
        self.fParticleGun = G4ParticleGun(n_particle)

        particle_table = G4ParticleTable.GetParticleTable()
        particle = particle_table.FindParticle("proton")

        self.fParticleGun.SetParticleDefinition(particle)
        self.fParticleGun.SetParticleMomentumDirection(G4ThreeVector(0., 0., 1.))
        self.fParticleGun.SetParticleEnergy(50*MeV)

    def GeneratePrimaries(self, anEvent: G4Event) -> None:
        
        env_sizeXY = 0
        env_sizeZ = 0
        
        if self.fEnvelopeBox == None:
            envLV = G4LogicalVolumeStore.GetInstance().GetVolume("Detector")

            if envLV != None:
                self.fEnvelopeBox = envLV.GetSolid()

            if self.fEnvelopeBox != None:
                env_sizeXY = self.fEnvelopeBox.GetXHalfLength()*2
                env_sizeZ = self.fEnvelopeBox.GetYHalfLength()*2
            else:
                print('EnvelopeBoxNotFoundException')
            
            x0 = 0
            y0 = 0
            z0 = -0.5*20*cm
            self.fParticleGun.SetParticlePosition(G4ThreeVector(x0, y0, z0))
            self.fParticleGun.GeneratePrimaryVertex(anEvent)
    
    
class MyActionInitializer(G4VUserActionInitialization):
    
    def Build(self) -> None:
        self.SetUserAction(MyPrimaryGenerationAction())
    
    
print('ebaniy geant')


runManager:G4RunManager = G4RunManagerFactory.CreateRunManager(G4RunManagerType.Serial)

runManager.SetUserInitialization(MyDetectorConstructor())

physics = FTFP_BERT()
physics.SetVerboseLevel(1)

runManager.SetUserInitialization(physics)

runManager.SetUserInitialization(MyActionInitializer())

runManager.Initialize()

ui = G4UIExecutive(len(sys.argv), sys.argv)
visManager = G4VisExecutive()
visManager.Initialize()

# Get the User Interface manager
UImanager = G4UImanager.GetUIpointer()
UImanager.ApplyCommand("/control/execute init_vis.mac")
ui.SessionStart()
#runManager.BeamOn(100)
