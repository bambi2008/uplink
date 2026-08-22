import UIKit
import Capacitor
import AVFoundation

@UIApplicationMain
class AppDelegate: UIResponder, UIApplicationDelegate {

    var window: UIWindow?
    private var audioRouteObserver: NSObjectProtocol?

    private func keepBuiltInAudioOnSpeaker() {
        let session = AVAudioSession.sharedInstance()
        guard session.currentRoute.outputs.contains(where: { $0.portType == .builtInReceiver }) else {
            return
        }
        do {
            try session.overrideOutputAudioPort(.speaker)
        } catch {
            NSLog("Uplink speaker routing deferred: %@", error.localizedDescription)
        }
    }

    private func configureCallAudio() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord,
                                    mode: .videoChat,
                                    options: [.defaultToSpeaker, .allowBluetoothHFP, .allowBluetoothA2DP])
            try session.setActive(true)
            keepBuiltInAudioOnSpeaker()
        } catch {
            // The web call still works if iOS temporarily owns the audio session.
            NSLog("Uplink audio session configuration deferred: %@", error.localizedDescription)
        }
    }

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        configureCallAudio()
        audioRouteObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: AVAudioSession.sharedInstance(),
            queue: .main
        ) { [weak self] _ in
            // Do not reset the category while WebKit is decoding or playing Jake's audio.
            // A route change can be emitted by our own speaker override, so only re-apply
            // the route after iOS has settled instead of recursively reconfiguring the session.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                self?.keepBuiltInAudioOnSpeaker()
            }
        }
        return true
    }

    func applicationWillResignActive(_ application: UIApplication) {
        // Sent when the application is about to move from active to inactive state. This can occur for certain types of temporary interruptions (such as an incoming phone call or SMS message) or when the user quits the application and it begins the transition to the background state.
        // Use this method to pause ongoing tasks, disable timers, and invalidate graphics rendering callbacks. Games should use this method to pause the game.
    }

    func applicationDidEnterBackground(_ application: UIApplication) {
        // Use this method to release shared resources, save user data, invalidate timers, and store enough application state information to restore your application to its current state in case it is terminated later.
        // If your application supports background execution, this method is called instead of applicationWillTerminate: when the user quits.
    }

    func applicationWillEnterForeground(_ application: UIApplication) {
        // Called as part of the transition from the background to the active state; here you can undo many of the changes made on entering the background.
    }

    func applicationDidBecomeActive(_ application: UIApplication) {
        configureCallAudio()
    }

    func applicationWillTerminate(_ application: UIApplication) {
        // Called when the application is about to terminate. Save data if appropriate. See also applicationDidEnterBackground:.
    }

    func application(_ application: UIApplication,
                     configurationForConnecting connectingSceneSession: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let config = UISceneConfiguration(name: "Default Configuration",
                                          sessionRole: connectingSceneSession.role)
        config.delegateClass = SceneDelegate.self
        return config
    }
}
