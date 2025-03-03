
import Vue from 'vue';
import VueRouter from 'vue-router';

import TVHome from '@/views/TV/Home.vue';
import TVWatch from '@/views/TV/Watch.vue';
import SettingsIndex from '@/views/Settings/Index.vue';
import SettingsGeneral from '@/views/Settings/General.vue';
import SettingsAccount from '@/views/Settings/Account.vue';
import SettingsJikkyo from '@/views/Settings/Jikkyo.vue';
import SettingsTwitter from '@/views/Settings/Twitter.vue';
import SettingsEnvironment from '@/views/Settings/Environment.vue';
import Login from '@/views/Login.vue';
import Register from '@/views/Register.vue';
import NotFound from '@/views/NotFound.vue';
import Utils from '@/utils';

Vue.use(VueRouter);

const router = new VueRouter({

    // History API モード
    mode: 'history',

    // ルーティングのベース URL
    base: process.env.BASE_URL,

    // ルーティング設定
    routes: [
        {
            path: '/',
            redirect: '/tv/',
        },
        {
            path: '/tv/',
            name: 'TV Home',
            component: TVHome,
        },
        {
            path: '/tv/watch/:channel_id',
            name: 'TV Watch',
            component: TVWatch,
        },
        {
            path: '/settings/',
            name: 'Settings Index',
            component: SettingsIndex,
            beforeEnter: (to, from, next) => {

                // スマホ縦画面・スマホ横画面・タブレット縦画面では設定一覧画面を表示する（画面サイズの関係）
                if (Utils.isSmartphoneVertical() || Utils.isSmartphoneHorizontal() || Utils.isTabletVertical()) {
                    next();  // 通常通り遷移
                    return;
                }

                // それ以外の画面サイズでは全般設定にリダイレクト
                next({path: '/settings/general/'});
            }
        },
        {
            path: '/settings/general',
            name: 'Settings General',
            component: SettingsGeneral,
        },
        {
            path: '/settings/account',
            name: 'Settings Account',
            component: SettingsAccount,
        },
        {
            path: '/settings/jikkyo',
            name: 'Settings Jikkyo',
            component: SettingsJikkyo,
        },
        {
            path: '/settings/twitter',
            name: 'Settings Twitter',
            component: SettingsTwitter,
        },
        {
            path: '/settings/environment',
            name: 'Settings Environment',
            component: SettingsEnvironment,
        },
        {
            path: '/login/',
            name: 'Login',
            component: Login,
        },
        {
            path: '/register/',
            name: 'Register',
            component: Register,
        },
        {
            path: '*',
            name: 'NotFound',
            component: NotFound,
        },
    ],

    // ページ遷移時のスクロールの挙動の設定
    // ref: https://v3.router.vuejs.org/ja/guide/advanced/scroll-behavior.html
    scrollBehavior (to, from, savedPosition) {
        if (savedPosition) {
            // 戻る/進むボタンが押されたときは保存されたスクロール位置を使う
            return savedPosition;
        } else {
            // それ以外は常に先頭にスクロールする
            return {x: 0, y: 0};
        }
    }
});

export default router;
