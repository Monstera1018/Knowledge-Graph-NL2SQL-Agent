import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

import { pageTransition } from './shared/animations/page-transitions';
import { createReducedMotionSignal } from './shared/utils/reduced-motion';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet],
  templateUrl: 'app.html',
  styleUrl: 'app.css',
  animations: [pageTransition],
})
export class App {
  protected readonly reduceMotion = createReducedMotionSignal();

  protected rootState(outlet: RouterOutlet): string {
    return outlet?.isActivated ? (outlet.activatedRoute.snapshot.routeConfig?.path ?? '') : '';
  }
}
